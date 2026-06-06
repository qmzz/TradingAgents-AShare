import copy
import threading
import tradingagents.default_config as default_config
from typing import Dict, Optional

_config_lock = threading.Lock()
_config: Optional[Dict] = None

def initialize_config():
    global _config
    with _config_lock:
        if _config is None:
            _config = default_config.DEFAULT_CONFIG.copy()

def set_config(config: Dict):
    global _config
    with _config_lock:
        if _config is None:
            _config = default_config.DEFAULT_CONFIG.copy()
        _config.update(config)

def get_config() -> Dict:
    with _config_lock:
        if _config is None:
            initialize_config()
        return copy.deepcopy(_config)

initialize_config()
