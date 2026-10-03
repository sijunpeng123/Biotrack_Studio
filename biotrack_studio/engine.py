import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QProgressDialog
from napari.utils.notifications import show_error, show_info
from .env_manager import get_env_cmd, get_subprocess_env

os.environ.setdefault("PYTHONNOUSERSITE", "1")

class SafeRunner:
    @staticmethod
    def run_step(env_name, script_path, args, step_name):
        env_cmd = get_env_cmd()
        cmd = [env_cmd, "run", "--no-capture-output", "-n", env_name, "python", script_path] + args
        output_dir = SafeRunner._infer_output_dir(args)
        log_path = SafeRunner._make_log_path(output_dir, step_name)
        last_lines = []

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=get_subprocess_env(),
            )

            output_queue = queue.Queue()
            reader = threading.Thread(
                target=SafeRunner._read_process_output,
                args=(process, output_queue),
                daemon=True,
            )
            reader.start()

            dialog = QProgressDialog(
                f"Starting {step_name}...",
                "Cancel",
                0,
                0,
            )
            dialog.setWindowTitle("BioTrack Studio Pipeline")
            dialog.setWindowModality(Qt.NonModal)
            dialog.setMinimumDuration(0)
            dialog.setMinimumWidth(560)
            dialog.show()

            start = time.time()
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                log_file.write("Command:\n")
                log_file.write(" ".join(str(part) for part in cmd) + "\n\n")
                log_file.flush()

                while process.poll() is None:
                    SafeRunner._drain_output(output_queue, log_file, last_lines)
                    elapsed = SafeRunner._format_elapsed(time.time() - start)
                    dialog.setLabelText(
                        SafeRunner._format_status_text(step_name, env_name, elapsed, last_lines, log_path)
                    )
                    QApplication.processEvents()

                    if dialog.wasCanceled():
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        show_error(f"Cancelled: {step_name}\nLog: {log_path}")
                        return False

                    time.sleep(0.2)

                SafeRunner._drain_output(output_queue, log_file, last_lines)
                dialog.close()

            if process.returncode == 0:
                show_info(f"Finished: {step_name}")
                return True
            show_error(
                f"Failed: {step_name}\n"
                "Please check the terminal output or contact the developer."
            )
            return False
        except Exception as e:
            show_error(f"Error: {str(e)}")
            return False

    @staticmethod
    def _read_process_output(process, output_queue):
        if process.stdout is None:
            return
        for line in process.stdout:
            output_queue.put(line.rstrip())

    @staticmethod
    def _drain_output(output_queue, log_file, last_lines):
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break
            log_file.write(line + "\n")
            log_file.flush()
            last_lines.append(line)
            del last_lines[:-20]

    @staticmethod
    def _infer_output_dir(args):
        for arg in reversed(args):
            try:
                path = Path(arg)
            except TypeError:
                continue
            if path.exists() and path.is_dir():
                return path
        return Path.cwd()

    @staticmethod
    def _make_log_path(output_dir, step_name):
        logs_dir = Path(output_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(step_name)).strip("_") or "step"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return logs_dir / f"{timestamp}_{safe_name}.log"

    @staticmethod
    def _format_elapsed(seconds):
        seconds = int(seconds)
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"

    @staticmethod
    def _format_status_text(step_name, env_name, elapsed, last_lines, log_path):
        return (
            f"Running: {step_name}\n"
            f"Elapsed: {elapsed}\n"
            "This may take several minutes. Please keep napari open."
        )

    @staticmethod
    def _truncate_line(line, limit=140):
        line = str(line)
        if len(line) <= limit:
            return line
        return line[: limit - 3] + "..."
