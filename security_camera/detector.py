from __future__ import annotations

import logging
import threading
import heapq
import subprocess
import time

import cv2


double_check_log = logging.getLogger("double_check")


class PersonDetector:
    """YOLO detector. It deliberately receives original capture frames, never 144p frames."""
    minimum_verification_batch_size = 16
    initial_verification_batch_size = 32
    maximum_verification_batch_size = 256
    verification_tuning_interval_seconds = 60.0
    verification_tuning_batches = 1
    verification_width = 640
    def __init__(self, confidence: float, device: str = "auto", processing_batch_size: int | str = "auto"):
        self.confidence = confidence
        self.error: str | None = None
        self.model = None
        self.device = self._select_device(device)
        self.use_half = self.device != "cpu"
        self.processing_batch_size = processing_batch_size
        self.current_verification_batch_size = self.minimum_verification_batch_size
        self._verification_performance = {}
        self._verification_failed_sizes = set()
        self._verification_last_tuning_at = 0.0
        self._gpu_usage_cache = (0.0, 0.0)
        self._lock = threading.Lock()
        try:
            from ultralytics import YOLO
            self.model = YOLO("yolo11n.pt")
            self.model.to(self.device)
            logging.info("YOLO person detector loaded on %s%s", self.device, " (FP16)" if self.use_half else "")
        except Exception as exc:
            self.error = f"YOLO unavailable: {exc}"
            logging.warning(self.error)

    @staticmethod
    def _select_device(requested: str) -> str:
        requested = str(requested).strip().lower()
        if requested == "cpu":
            return "cpu"
        try:
            import torch
            if not torch.cuda.is_available():
                logging.info("CUDA unavailable; using CPU for YOLO")
                return "cpu"
            count = torch.cuda.device_count()
            index = 0 if requested == "auto" else int(requested)
            if index < 0 or index >= count:
                raise ValueError(f"GPU index {index} is unavailable")
            logging.info("Using CUDA GPU %s: %s", index, torch.cuda.get_device_name(index))
            return f"cuda:{index}"
        except (ImportError, ValueError, RuntimeError) as exc:
            logging.warning("GPU selection failed (%s); using CPU for YOLO", exc)
            return "cpu"

    @property
    def available(self) -> bool:
        return self.model is not None

    def gpu_memory_percent(self) -> float:
        if not self.device.startswith("cuda"):
            return 0.0
        try:
            import torch
            total = torch.cuda.get_device_properties(self.device).total_memory
            return 100.0 * torch.cuda.memory_allocated(self.device) / max(1, total)
        except (RuntimeError, ImportError):
            return 0.0

    def gpu_utilization_percent(self) -> float:
        """Return CUDA engine utilization, not the application's allocated memory."""
        if not self.device.startswith("cuda"):
            return 0.0
        now = time.monotonic()
        cached_at, cached_value = self._gpu_usage_cache
        if now - cached_at < 1.0:
            return cached_value
        try:
            gpu_index = self.device.split(":", 1)[1]
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits", "--id", gpu_index],
                capture_output=True, text=True, timeout=0.5, check=True,
            )
            value = float(result.stdout.strip().splitlines()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            value = 0.0
        self._gpu_usage_cache = (now, value)
        return value

    def has_person(self, frame) -> bool:
        if not self.model:
            return False
        try:
            result = self._detect(frame)
            return len(result.boxes) > 0
        except Exception as exc:
            self.error = f"Detection error: {exc}"
            logging.exception("Detector failure")
            return False

    def _detect(self, frame):
        # The live and recording verification paths share one model instance.
        with self._lock:
            return self.model(
                frame, classes=[0], conf=self.confidence, device=self.device,
                quantize=16 if self.use_half else 32, verbose=False,
            )[0]

    def _detect_batch(self, frames):
        with self._lock:
            return self.model(
                frames, classes=[0], conf=self.confidence, device=self.device,
                quantize=16 if self.use_half else 32, verbose=False,
            )

    def _available_batch_size(self):
        """Return the learned batch size, starting small for responsive checks."""
        if not self.minimum_verification_batch_size <= self.current_verification_batch_size <= self.maximum_verification_batch_size:
            self.current_verification_batch_size = self.minimum_verification_batch_size
        return self.current_verification_batch_size

    def _tune_verification_batch(self, batch_size, elapsed, performance, failed_sizes):
        """Adjust batch size from measured throughput without blocking preflight work."""
        samples = performance.setdefault(batch_size, [])
        samples.append(batch_size / max(0.0001, elapsed))
        if len(samples) < self.verification_tuning_batches:
            return batch_size
        measured_rate = sum(samples) / len(samples)
        samples.clear()
        previous_size = batch_size // 2
        previous_rate = performance.get(previous_size)
        if previous_rate is not None and measured_rate < previous_rate * 0.95:
            failed_sizes.add(batch_size)
            next_size = previous_size
            double_check_log.info(
                "BATCH_REDUCED batch=%s frames_per_sec=%.1f previous=%.1f",
                next_size, measured_rate, previous_rate,
            )
        elif batch_size < self.maximum_verification_batch_size:
            performance[batch_size] = measured_rate
            next_size = min(self.maximum_verification_batch_size, batch_size * 2)
            if next_size in failed_sizes:
                return batch_size
            double_check_log.info(
                "BATCH_PROBE batch=%s frames_per_sec=%.1f previous=%s",
                next_size, measured_rate, batch_size,
            )
        else:
            performance[batch_size] = measured_rate
            return batch_size
        self.current_verification_batch_size = next_size
        return next_size

    def benchmark_verification_batch(self, frames):
        """Find the fastest batch by doubling until memory or throughput gives out."""
        if not self.model or not frames:
            return self.current_verification_batch_size
        best_size = self.minimum_verification_batch_size
        best_rate = 0.0
        candidate = best_size
        while True:
            batch = (frames * ((candidate + len(frames) - 1) // len(frames)))[:candidate]
            try:
                if self.device.startswith("cuda"):
                    import torch
                    torch.cuda.synchronize(self.device)
                started = time.perf_counter()
                self._detect_batch(batch)
                if self.device.startswith("cuda"):
                    torch.cuda.synchronize(self.device)
                elapsed = max(0.0001, time.perf_counter() - started)
            except (RuntimeError, MemoryError) as exc:
                if "out of memory" not in str(exc).lower() and not isinstance(exc, MemoryError):
                    logging.warning("Batch benchmark stopped: %s", exc)
                if self.device.startswith("cuda"):
                    torch.cuda.empty_cache()
                break
            rate = candidate / elapsed
            double_check_log.info("BENCHMARK batch=%s elapsed=%.3fs frames_per_sec=%.1f", candidate, elapsed, rate)
            if rate > best_rate:
                best_size, best_rate = candidate, rate
            elif candidate > best_size and rate < best_rate * 0.90:
                break
            if candidate >= self.maximum_verification_batch_size:
                break
            candidate = min(self.maximum_verification_batch_size, candidate * 2)
        self.current_verification_batch_size = best_size
        double_check_log.info("BATCH_SELECTED batch=%s frames_per_sec=%.1f", best_size, best_rate)
        return best_size

    @classmethod
    def _verification_frame(cls, frame):
        height, width = frame.shape[:2]
        if width <= cls.verification_width:
            return frame
        target_height = max(1, round(height * cls.verification_width / width))
        return cv2.resize(frame, (cls.verification_width, target_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _adaptive_sample_batches(frame_count: int, initial_points: int = 3, points_per_round: int = 3):
        if frame_count <= 0:
            return
        if frame_count <= initial_points:
            yield list(range(frame_count))
            return

        targets = {round(index * (frame_count - 1) / (initial_points - 1)) for index in range(initial_points)}
        yield sorted(targets)
        gaps = []
        ordered = sorted(targets)
        for left, right in zip(ordered, ordered[1:]):
            heapq.heappush(gaps, (-(right - left), left, right))

        while gaps:
            additions = []
            for _ in range(points_per_round):
                if not gaps:
                    break
                negative_length, left, right = heapq.heappop(gaps)
                if right - left <= 1:
                    continue
                midpoint = (left + right) // 2
                targets.add(midpoint)
                heapq.heappush(gaps, (-(midpoint - left), left, midpoint))
                heapq.heappush(gaps, (-(right - midpoint), midpoint, right))
                additions.append(midpoint)
            if not additions:
                break
            yield sorted(additions)

    def video_has_person(self, path, sample_every: int | None = 30, progress=None) -> bool | None:
        """Sample a completed recording; return None when verification is unavailable.

        When ``sample_every`` is None, three frames are checked at a time and
        the largest unchecked gaps are progressively subdivided.
        """
        if not self.model:
            return None
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            logging.warning("Unable to open recording for verification: %s", path)
            capture.release()
            return None
        try:
            frame_count = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            if sample_every is None and frame_count:
                for batch in self._adaptive_sample_batches(frame_count):
                    for target in batch:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, target)
                        ok, frame = capture.read()
                        if not ok:
                            continue
                        if progress is not None and progress(target + 1) is False:
                            return None
                        try:
                            result = self._detect(frame)
                        except Exception as exc:
                            self.error = f"Recording verification error: {exc}"
                            logging.exception("Recording verification failure for %s", path)
                            return None
                        if len(result.boxes) > 0:
                            return True
                return False

            frame_index = 0
            sampled_frames = 0
            batch_frames = []
            batch_size = min(self._available_batch_size(), self.maximum_verification_batch_size)
            while True:
                ok, frame = capture.read()
                if not ok:
                    if batch_frames:
                        try:
                            results = self._detect_batch(batch_frames)
                        except Exception as exc:
                            self.error = f"Recording verification error: {exc}"
                            logging.exception("Recording verification failure for %s", path)
                            return None
                        if any(len(result.boxes) > 0 for result in results):
                            return True
                    return False
                should_detect = frame_index % max(1, sample_every) == 0
                if should_detect:
                    sampled_frames += 1
                    if progress is not None and progress(sampled_frames) is False:
                        return None
                    batch_frames.append(self._verification_frame(frame))
                    if len(batch_frames) >= batch_size:
                        started = time.perf_counter()
                        try:
                            results = self._detect_batch(batch_frames)
                        except Exception as exc:
                            if "out of memory" in str(exc).lower() and batch_size > self.minimum_verification_batch_size:
                                batch_size = max(self.minimum_verification_batch_size, batch_size // 2)
                                self.current_verification_batch_size = batch_size
                                double_check_log.warning("BATCH_REDUCED batch=%s reason=GPU_OUT_OF_MEMORY", batch_size)
                                try:
                                    results = self._detect_batch(batch_frames[:batch_size])
                                except Exception:
                                    self.error = f"Recording verification error: {exc}"
                                    logging.exception("Recording verification failure for %s", path)
                                    return None
                                batch_frames = batch_frames[batch_size:]
                            else:
                                self.error = f"Recording verification error: {exc}"
                                logging.exception("Recording verification failure for %s", path)
                                return None
                        if any(len(result.boxes) > 0 for result in results):
                            return True
                        if len(batch_frames) >= batch_size:
                            batch_frames.clear()
                        now = time.monotonic()
                        if (not batch_frames
                                and now - self._verification_last_tuning_at >= self.verification_tuning_interval_seconds):
                            self._verification_last_tuning_at = now
                            batch_size = self._tune_verification_batch(
                                batch_size, time.perf_counter() - started,
                                self._verification_performance, self._verification_failed_sizes,
                            )
                frame_index += 1
        except Exception as exc:
            self.error = f"Recording verification error: {exc}"
            logging.exception("Recording verification failure for %s", path)
            return None
        finally:
            capture.release()

    def video_person_frame_count(self, path, progress=None) -> int | None:
        """Check every frame and return how many frames contain a person."""
        if not self.model:
            return None
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            logging.warning("Unable to open recording for verification: %s", path)
            capture.release()
            return None
        person_frames = 0
        frame_count = 0
        batch_frames = []
        auto_batch = str(self.processing_batch_size).strip().lower() == "auto"
        batch_size = 32 if auto_batch else min(max(1, int(self.processing_batch_size)), self.maximum_verification_batch_size)

        def detect_batch() -> None:
            nonlocal person_frames, batch_size
            if not batch_frames:
                return
            pending = list(batch_frames)
            batch_frames.clear()
            while pending:
                current_size = min(batch_size, len(pending))
                current = pending[:current_size]
                while True:
                    try:
                        results = self._detect_batch(current)
                        break
                    except (RuntimeError, MemoryError) as exc:
                        if not auto_batch or current_size <= 1 or "out of memory" not in str(exc).lower():
                            raise
                        current_size = max(1, current_size // 2)
                        current = pending[:current_size]
                        batch_size = current_size
                        try:
                            import torch
                            torch.cuda.empty_cache()
                        except (ImportError, RuntimeError):
                            pass
                if auto_batch and self.device.startswith("cuda"):
                    try:
                        import torch
                        total_memory = torch.cuda.get_device_properties(self.device).total_memory
                        used_memory = torch.cuda.memory_reserved(self.device)
                        usage = used_memory / max(1, total_memory)
                        if usage < 0.60:
                            batch_size = min(self.maximum_verification_batch_size, max(batch_size + 1, batch_size * 2))
                        elif usage > 0.85:
                            batch_size = max(1, batch_size // 2)
                    except (ImportError, RuntimeError):
                        pass
                person_frames += sum(len(result.boxes) > 0 for result in results)
                pending = pending[current_size:]

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    detect_batch()
                    return person_frames
                frame_count += 1
                if progress is not None and progress(frame_count) is False:
                    return None
                batch_frames.append(self._verification_frame(frame))
                if len(batch_frames) >= batch_size:
                    detect_batch()
        except Exception as exc:
            self.error = f"Recording verification error: {exc}"
            logging.exception("Recording verification failure for %s", path)
            return None
        finally:
            capture.release()
