import json
import threading
import queue
import subprocess
import time
import os
import logging
from typing import Any, Dict, List, Optional


class MCPClientError(Exception):
    pass


class MCPStdIOClient:
    """JSON-RPC 2.0 client for FastMCP servers using stdio transport.
    
    Usage:
        # start server as a module
        client = MCPStdIOClient([sys.executable, "-m", "weather.server"], cwd=".")
        client.start()
        result = client.call_tool("get_forecast", {"latitude": 37.77, "longitude": -122.42})
        client.stop()
    """

    def __init__(self, command: List[str], cwd: str = ".", timeout: float = 10.0, log_file: Optional[str] = None, log_level: int = logging.INFO):
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._pending: Dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

        # Logger setup: log MCP server stdout/stderr to a file inside LOG_DIR (default 'logs')
        log_dir = os.environ.get("LOG_DIR", "logs")
        if log_file:
            self.log_file = os.path.abspath(log_file)
        else:
            self.log_file = os.path.abspath(os.path.join(log_dir, "mcp_server.log"))
        # Ensure the directory for the log file exists
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        except Exception:
            pass

        self.logger = logging.getLogger("mcp_client")
        # Avoid duplicate handlers if multiple clients are created
        if self.log_file:
            handler_exists = False
            for h in self.logger.handlers:
                try:
                    if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == self.log_file:
                        handler_exists = True
                        break
                except Exception:
                    continue
            if not handler_exists:
                handler = logging.FileHandler(self.log_file)
                handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
                self.logger.addHandler(handler)
            self.logger.setLevel(log_level)
            # Prevent propagation to the root logger to avoid duplicate console output
            self.logger.propagate = False
        else:
            # Default: silence logging if no file specified
            self.logger.addHandler(logging.NullHandler())

    def start(self) -> None:
        if self.proc:
            return

        self.proc = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )

        # Start reader thread
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Attach stderr reader to see server logs
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        # Initialize the MCP connection
        time.sleep(0.2)
        try:
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "python-mcp-client", "version": "1.0.0"}
            }
            resp = self._send_request("initialize", init_params)
            # Initialization completed
            # Send initialized notification
            self._send_notification("notifications/initialized")
        except MCPClientError:
            # Initialization failed; continue without noisy output
            pass

    def stop(self) -> None:
        self._running = False
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def _stderr_loop(self) -> None:
        if not self.proc:
            return
        try:
            with self.proc.stderr:
                for line in iter(self.proc.stderr.readline, b""):
                    try:
                        msg = line.decode("utf-8", errors="ignore").rstrip()
                        if msg and not msg.startswith('['):
                            self.logger.info(f"[MCP server] {msg}")
                        else:
                            self.logger.info(msg)
                    except Exception:
                        pass
        except Exception:
            pass

    def _reader_loop(self) -> None:
        """Read newline-delimited JSON messages from stdout."""
        if not self.proc or not self.proc.stdout:
            return

        try:
            while self._running:
                line = self.proc.stdout.readline()
                if not line:
                    if self.proc.poll() is not None:
                        break
                    time.sleep(0.01)
                    continue
                
                try:
                    text = line.decode('utf-8', errors='ignore').strip()
                    if not text:
                        continue
                    
                    # Try to parse as JSON
                    msg_dict = json.loads(text)
                    self._handle_message(msg_dict)
                    
                except json.JSONDecodeError:
                    # Not JSON, might be server output
                    if text and not text.startswith('['):
                        self.logger.info(f"[MCP server output] {text}")
                except Exception:
                    # Ignore processing errors silently
                    continue
                        
        except Exception:
            return

    def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming JSON-RPC message."""
        # Handle responses (has 'id')
        msg_id = message.get('id')
        if msg_id is not None:
            with self._lock:
                q = self._pending.get(msg_id)
            if q:
                q.put(message)
            else:
                self.logger.warning(f"[MCP client] Received response for unknown id: {msg_id}")
        else:
            # Notification - ignored in silent mode
            return

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _send_notification(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.proc or self.proc.poll() is not None:
            raise MCPClientError('MCP server is not running')
            
        request = {
            "jsonrpc": "2.0",
            "method": method
        }
        if params is not None:
            request["params"] = params
            
        self._write_message(request)

    def _send_request(self, method: str, params: Any = None) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self.proc or self.proc.poll() is not None:
            raise MCPClientError('MCP server is not running')

        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id
        }
        if params is not None:
            request["params"] = params

        q: queue.Queue = queue.Queue()
        with self._lock:
            self._pending[req_id] = q

        try:
            self._write_message(request)
            
            # Wait for response
            try:
                msg = q.get(timeout=self.timeout)
            except queue.Empty:
                raise MCPClientError(f'Timeout waiting for response to {method}')

            # Handle error
            if 'error' in msg:
                error = msg['error']
                raise MCPClientError(f"{error.get('message', 'Unknown error')}")

            # Return result
            return msg.get('result')

        finally:
            with self._lock:
                if req_id in self._pending:
                    del self._pending[req_id]

    def _write_message(self, message: Dict[str, Any]) -> None:
        """Write a newline-delimited JSON message to stdin."""
        payload = json.dumps(message) + "\n"
        
        method = message.get('method', f"response-{message.get('id')}")
        try:
            with self._write_lock:
                self.proc.stdin.write(payload.encode('utf-8'))
                self.proc.stdin.flush()
        except Exception as e:
            raise MCPClientError(f"Failed to write to MCP server: {e}")

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool (high-level method)."""
        params = {
            "name": tool_name,
            "arguments": arguments
        }
        result = self._send_request("tools/call", params)
        
        # FastMCP returns result with 'content' array
        if isinstance(result, dict) and 'content' in result:
            content = result['content']
            if isinstance(content, list) and len(content) > 0:
                # Return the text from first content item
                first_item = content[0]
                if isinstance(first_item, dict):
                    return first_item.get('text', str(first_item))
                return str(first_item)
        
        return result

    def call_method(self, method: str, params: Any) -> Any:
        """Generic method call (use call_tool for tools)."""
        return self._send_request(method, params)
