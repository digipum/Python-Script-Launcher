import sys
import os
import json
import subprocess
import pty
import fcntl
import select
import signal
import termios
import struct
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                            QPushButton, QLabel, QListWidget, QFileDialog, QMessageBox,
                            QFrame, QStatusBar, QSplitter, QTextEdit, QSizePolicy, QLineEdit)
from PyQt6.QtCore import Qt, QSize, QProcess, QTimer, pyqtSignal, pyqtSlot, QIODevice
from PyQt6.QtGui import QFont, QColor, QTextCursor, QPalette, QKeyEvent

class InteractiveTerminal(QWidget):
    """Interactive terminal widget that captures subprocess output and allows input."""

    processFinished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Output area
        self.output = QTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Courier New", 10))

        # Set colors for terminal-like appearance
        palette = self.output.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0))  # Black background
        palette.setColor(QPalette.ColorRole.Text, QColor(170, 170, 170))  # Light gray text
        self.output.setPalette(palette)

        # No Input line in InteractiveTerminal anymore - input is handled by ScriptLauncherApp

        # Clear button
        button_layout = QHBoxLayout()
        self.clear_button = QPushButton("Clear Terminal")
        self.clear_button.clicked.connect(self.clear_terminal)
        button_layout.addStretch(1)
        button_layout.addWidget(self.clear_button)

        # Add widgets to layout
        layout.addWidget(self.output, 1)
        # input_layout removed from here
        layout.addLayout(button_layout)

        # Process management
        self.process = None
        self.master_fd = None
        self.slave_fd = None
        self.is_running = False

        # Input/output handling
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_pty)

        # Initial state
        # input_field disabled here is also removed, as no input field in this widget anymore

    def clear_terminal(self):
        """Clear the terminal output."""
        self.output.clear()

    def run_command(self, command, args=None, cwd=None):
        """Run a command in the terminal."""
        self.clear_terminal()
        command_str = f"{command} {' '.join(args) if args else ''}" if args else command
        self.append(f"$ {command_str}\n")

        # Stop any existing process
        self.stop_process()

        try:
            # Create pseudo-terminal
            self.master_fd, self.slave_fd = pty.openpty()

            # Make the PTY non-blocking
            fl = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            # Set terminal attributes for proper line handling
            attrs = termios.tcgetattr(self.slave_fd)
            attrs[3] = attrs[3] & ~termios.ECHO  # Turn off terminal echo
            termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs)

            # Prepare command
            cmd_args = [command]
            if args:
                cmd_args.extend(args)

            # Start the process
            self.is_running = True
            self.process = subprocess.Popen(
                cmd_args,
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                cwd=cwd,
                preexec_fn=os.setsid,
                universal_newlines=True
            )

            # Start timer to read output
            self.timer.start(50)  # Check for output every 50ms

            # Input is now handled by the ScriptLauncherApp's args_input field, no need to enable input_field here.

        except Exception as e:
            self.append(f"Error starting process: {str(e)}\n")
            self.is_running = False
            self.processFinished.emit()

    def send_input(self, text): # Modified to accept text as argument
        """Send input to the process."""
        if not self.is_running or self.master_fd is None:
            return

        if not text:
            return

        # Echo input to the terminal - remove prompt as it's input during script run
        self.append(text + "\n")

        # Send to process with newline
        try:
            os.write(self.master_fd, (text + "\n").encode())
        except OSError as e:
            self.append(f"Error sending input: {str(e)}\n")

        # Clear input field -  Clear is now done in ScriptLauncherApp after sending input


    def read_pty(self):
        """Read from the PTY master file descriptor."""
        if not self.is_running or self.master_fd is None:
            self.timer.stop()
            return

        try:
            # Check if the process is still running
            if self.process and self.process.poll() is not None:
                self.append(f"\nProcess exited with code {self.process.returncode}\n")
                self.stop_process()
                return

            # Check for output
            rlist, _, _ = select.select([self.master_fd], [], [], 0)
            if self.master_fd in rlist:
                try:
                    data = os.read(self.master_fd, 1024)
                    if data:
                        text = data.decode('utf-8', errors='replace')
                        self.append_text(text)
                    else:
                        # EOF reached
                        self.stop_process()
                except OSError as e:
                    if e.errno == 5:  # Input/output error - likely child process terminated
                        self.stop_process()
                    else:
                        self.append(f"PTY error: {str(e)}\n")
                        self.stop_process()
        except Exception as e:
            self.append(f"Error reading from PTY: {str(e)}\n")
            self.stop_process()

    def stop_process(self):
        """Stop the current process and clean up."""
        self.is_running = False
        self.timer.stop()

        # Kill process if it's still running
        if self.process:
            try:
                if self.process.poll() is None:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass  # Process already terminated
            self.process = None

        # Close file descriptors
        if self.master_fd is not None:
            os.close(self.master_fd)
            self.master_fd = None

        if self.slave_fd is not None:
            os.close(self.slave_fd)
            self.slave_fd = None

        # Input field is managed in ScriptLauncherApp
        self.processFinished.emit()


    def append_text(self, text, error=False):
        """Append text to the terminal with proper formatting."""
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if error:
            format = cursor.charFormat()
            format.setForeground(QColor(255, 100, 100))  # Light red for errors
            cursor.setCharFormat(format)

        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def append(self, text):
        """Append text with a newline."""
        self.append_text(text)

    # keyPressEvent is no longer needed as input is handled by ScriptLauncherApp's args_input


class ScriptLauncherApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Script Launcher")
        self.setMinimumSize(800, 600)

        self.scripts = []
        self.config_file = "/Users/cfrstudio/Dropbox/CFR/SCRIPTS AND TERMINAL/SCRIPT LAUNCHER/script_launcher_config.json"

        self.setup_ui()
        self.load_scripts()

    def setup_ui(self):
        # Central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Title
        title_label = QLabel("Python Script Launcher")
        title_font = QFont("Helvetica", 16)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        # Splitter for script list and terminal
        splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(splitter, 1)

        # Upper widget for script controls
        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        upper_layout.setContentsMargins(0, 0, 0, 0)

        # Buttons layout
        button_layout = QHBoxLayout()
        add_button = QPushButton("Add Script")
        add_button.clicked.connect(self.add_script)
        remove_button = QPushButton("Remove Script")
        remove_button.clicked.connect(self.remove_script)

        button_layout.addWidget(add_button)
        button_layout.addWidget(remove_button)
        button_layout.addStretch()

        upper_layout.addLayout(button_layout)

        # Scripts list
        list_label = QLabel("Available Scripts")
        upper_layout.addWidget(list_label)

        self.scripts_list = QListWidget()
        self.scripts_list.setAlternatingRowColors(True)
        self.scripts_list.itemDoubleClicked.connect(self.run_script)  # Double-click to run
        upper_layout.addWidget(self.scripts_list, 1)


        # Script arguments input layout (now for interactive input)
        self.script_args_layout = QHBoxLayout()
        args_label = QLabel("Script Input:") # Relabeled
        self.args_input = QLineEdit()
        self.args_input.returnPressed.connect(self.send_terminal_input) # Connect returnPressed signal
        self.script_args_layout.addWidget(args_label)
        self.script_args_layout.addWidget(self.args_input, 1)


        upper_layout.addLayout(self.script_args_layout)

        # Run button
        run_button = QPushButton("Run Selected Script")
        run_button.clicked.connect(self.run_script)
        upper_layout.addWidget(run_button)

        splitter.addWidget(upper_widget)

        # Terminal widget
        terminal_label = QLabel("Terminal Output")
        terminal_layout = QVBoxLayout()
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addWidget(terminal_label)

        self.terminal = InteractiveTerminal()
        terminal_layout.addWidget(self.terminal, 1)

        terminal_widget = QWidget()
        terminal_widget.setLayout(terminal_layout)
        splitter.addWidget(terminal_widget)

        # Set initial splitter proportions
        splitter.setSizes([300, 400])

        # Status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

        # Connect signals
        self.terminal.processFinished.connect(self.process_finished)

    def send_terminal_input(self):
        """Sends input from the args_input field to the terminal."""
        text = self.args_input.text()
        if text:
            self.terminal.send_input(text) # Call terminal's send_input
            self.args_input.clear() # Clear the input field after sending


    def add_script(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Python Script",
            "",
            "Python Files (*.py);;All Files (*.*)"
        )

        if filepath:
            name = os.path.basename(filepath)
            self.scripts.append({"name": name, "path": filepath})
            self.update_scripts_list()
            self.save_scripts()
            self.statusBar.showMessage(f"Added script: {name}")

    def remove_script(self):
        selected_row = self.scripts_list.currentRow()
        if selected_row >= 0:
            script = self.scripts[selected_row]
            del self.scripts[selected_row]
            self.update_scripts_list()
            self.save_scripts()
            self.statusBar.showMessage(f"Removed script: {script['name']}")

    def run_script(self):
        selected_row = self.scripts_list.currentRow()
        if selected_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a script to run.")
            return

        script = self.scripts[selected_row]
        self.statusBar.showMessage(f"Running: {script['name']}...")

        try:
            # Get the directory of the script
            script_dir = os.path.dirname(script['path'])

            # No script arguments from args_input at start anymore - it's for interactive input now.

            # Run the script in the integrated terminal
            python_path = sys.executable
            self.terminal.run_command(python_path, [script['path']], cwd=script_dir) # No arguments passed initially

        except Exception as e:
            self.statusBar.showMessage(f"Error running {script['name']}: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to run script: {str(e)}")

    def process_finished(self):
        """Handle process completion."""
        self.statusBar.showMessage("Process finished")

    def update_scripts_list(self):
        self.scripts_list.clear()
        for script in self.scripts:
            self.scripts_list.addItem(f"{script['name']} ({script['path']})")

    def save_scripts(self):
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.scripts, f)
        except Exception as e:
            self.statusBar.showMessage(f"Error saving configuration: {str(e)}")

    def load_scripts(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.scripts = json.load(f)
                    self.update_scripts_list()
        except Exception as e:
            self.statusBar.showMessage(f"Error loading configuration: {str(e)}")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    mainWin = ScriptLauncherApp()
    mainWin.show()
    sys.exit(app.exec())