"""
Bypass recording orchestrator for LeRobot AlohaMini dataset collection.
Guarantees:
1. --dataset.push_to_hub=false (bypasses HuggingFace, writes strictly local)
2. Subsumes client recording role in single daemon
3. Zero modifications to LeRobot source
"""

from __future__ import annotations

import json
import os
import sys
import time
import signal
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable
from agent.config import AgentConfig

logger = logging.getLogger(__name__)


class RecordingSession:
    """Manages the lifecycle of an automated or teleoperated dataset recording run."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.state: str = "idle"  # idle, recording, paused, uploading, completed, error
        self.experiment_id: str | None = None
        self.task_description: str = "Default task"
        self.current_episode: int = 0
        self.target_episodes: int = 50
        self.output_dir: Path | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._on_episode_complete: Callable[[int, Path], None] | None = None

    def start_recording(
        self,
        experiment_id: str,
        target_episodes: int = 50,
        task_description: str = "AlohaMini manipulation task",
        on_episode_complete: Callable[[int, Path], None] | None = None,
    ) -> bool:
        """Start a new recording session."""
        with self._lock:
            if self.state == "recording":
                logger.warning("Recording session already in progress.")
                return False

            self.experiment_id = experiment_id
            self.target_episodes = target_episodes
            self.task_description = task_description
            self.current_episode = 0
            self._on_episode_complete = on_episode_complete

            # Output dataset directory: <dataset_root_dir>/<experiment_id>
            self.output_dir = self.config.dataset_root_dir / experiment_id
            self.output_dir.mkdir(parents=True, exist_ok=True)

            self.state = "recording"
            logger.info(
                "Starting recording session for experiment %s, target: %d episodes, dir: %s",
                experiment_id,
                target_episodes,
                self.output_dir,
            )
            return True

    def build_record_command(
        self,
        repo_id: str = "alohamini_dataset",
        episode_time_s: int = 30,
    ) -> list[str]:
        """
        Build the CLI command to launch record_bi.py with strictly controlled flags:
        - MUST include --dataset.push_to_hub=false
        - MUST specify local root directory
        """
        script_path = Path(__file__).resolve().parent.parent / "examples" / "alohamini" / "record_bi.py"
        cmd = [
            sys.executable,
            str(script_path),
            f"--dataset.repo_id={repo_id}",
            f"--dataset.root={self.output_dir}",
            "--dataset.push_to_hub=false",  # Requirement 1: bypass HF push
            f"--dataset.num_episodes={self.target_episodes}",
            f"--dataset.fps={self.config.control_fps}",
            f"--dataset.episode_time_s={episode_time_s}",
            f"--dataset.single_task={self.task_description}",
        ]
        return cmd

    def stop_recording(self, force: bool = False) -> None:
        """Stop the recording session."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                logger.info("Terminating recording subprocess...")
                if force:
                    self._process.kill()
                else:
                    self._process.send_signal(signal.SIGINT)
                    try:
                        self._process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                self._process = None

            self.state = "completed" if self.current_episode >= self.target_episodes else "idle"
            logger.info("Recording session stopped. Current state: %s", self.state)

    def keep_episode(
        self,
        episode_index: int,
        tags: list[str] | None = None,
        note: str = "",
        quality_score: int = 5,
    ) -> bool:
        """
        Accept and finalize the completed episode into the dataset.
        Increments current_episode and records quality metadata.
        """
        with self._lock:
            self.current_episode = max(self.current_episode, episode_index + 1)
            manifest_file = (self.output_dir or self.config.dataset_root_dir) / "quality_manifest.json"
            record = {
                "episode_index": episode_index,
                "status": "accepted",
                "quality_score": quality_score,
                "tags": tags or ["clean"],
                "note": note,
                "timestamp": time.time(),
            }
            self._append_manifest(manifest_file, record)
            logger.info("Episode %d accepted with quality score %d", episode_index, quality_score)
            if self.current_episode >= self.target_episodes:
                self.state = "completed"
            return True

    def discard_current_episode(
        self,
        episode_index: int,
        reason: str = "operator_rejected",
    ) -> bool:
        """
        Discard the current episode and prepare for retake.
        Cleans up temporary slice files for this episode and does NOT advance current_episode,
        allowing the student to immediately re-record the same episode index.
        """
        with self._lock:
            manifest_file = (self.output_dir or self.config.dataset_root_dir) / "quality_manifest.json"
            record = {
                "episode_index": episode_index,
                "status": "discarded",
                "reason": reason,
                "timestamp": time.time(),
            }
            self._append_manifest(manifest_file, record)

            # Clean up potential local episode artifacts if present
            if self.output_dir:
                ep_pattern = f"*episode_{episode_index:06d}*"
                for f in self.output_dir.glob(ep_pattern):
                    try:
                        if f.is_file():
                            f.unlink()
                        logger.info("Cleaned up discarded episode file: %s", f.name)
                    except Exception as e:
                        logger.warning("Could not delete %s: %s", f, e)

            logger.info("Episode %d discarded for retake. Reason: %s", episode_index, reason)
            return True

    def _append_manifest(self, filepath: Path, record: dict[str, Any]) -> None:
        try:
            records = []
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    records = json.load(f)
            records.append(record)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to update quality manifest: %s", e)

    def get_status(self) -> dict[str, Any]:
        """Get the current session progress and quality stats."""
        with self._lock:
            manifest_file = (self.output_dir or self.config.dataset_root_dir) / "quality_manifest.json"
            manifest_count = 0
            if manifest_file.exists():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        manifest_count = len(json.load(f))
                except Exception:
                    pass

            return {
                "state": self.state,
                "experiment_id": self.experiment_id,
                "current_episode": self.current_episode,
                "target_episodes": self.target_episodes,
                "output_dir": str(self.output_dir) if self.output_dir else None,
                "manifest_entries": manifest_count,
            }
