import os
import sys
import importlib.util
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Dict, Any, Type
import logging

logger = logging.getLogger(__name__)

class PluginBase(ABC):
    """
    Abstract base class that all plugins must inherit from.
    Ensures a consistent interface for the plugin system.
    """

    def __init__(self):
        self.name = self.__class__.__name__
        self.version = "1.0.0"
        self.enabled = True

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the plugin. Return True if successful."""
        pass

    @abstractmethod
    def shutdown(self) -> bool:
        """Shutdown the plugin. Return True if successful."""
        pass

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Execute the plugin's main functionality."""
        pass

    def on_error(self, error: Exception) -> None:
        """Handle errors gracefully."""
        logger.error(f"Plugin {self.name} error: {error}")

class PluginManager:
    """
    Manages plugin lifecycle: loading, unloading, and execution.
    Works in context with FileManagerBase and ProcessingThread classes.

    Usage:
        manager = PluginManager("./plugins")
        manager.load_plugin("extension_filter.py")
        result = manager.execute("extension_filter", file_data)
        manager.unload_plugin("extension_filter")
    """

    def __init__(self, plugins_directory: str = "./plugins"):
        """
        Initialize the PluginManager.

        Args:
            :param plugins_directory: Path to folder containing .py plugin files
        """
        if plugins_directory is None:
            # Use absolute path relative to this file
            current_dir = Path(__file__).parent.absolute()
            self.plugins_directory = current_dir / "plugins"
        else:
            self.plugins_directory = Path(plugins_directory).resolve()

        self.plugins: Dict[str, PluginBase] = {}
        self.plugins_modules: Dict[str, Any] = {}

        # Ensure plugins directory exists
        self.plugins_directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"PluginManager initialized at {self.plugins_directory}")

    def load_plugin(self, plugin_filename: str) -> bool:
        """
        Dynamically load a plugin from a .py file.

        Args:
            :param plugin_filename: Name of the .py file (e.g. 'my_plugin.py')


        Returns:
            True if plugin loaded successfully, False otherwise
        """
        plugin_path = self.plugins_directory / plugin_filename

        if not plugin_path.exists():
            logger.error(f"Plugin file not found: {plugin_path}")
            return False

        try:
            plugin_key = plugin_filename.replace(".py", "")

            # Clean up any existing cached module
            if plugin_key in sys.modules:
                del sys.modules[plugin_key]

            # Load module dynamically
            spec = importlib.util.spec_from_file_location(
                plugin_filename.replace(".py", ""),
                plugin_path
            )

            if spec is None or spec.loader is None:
                logger.error(f"Could not load spec for {plugin_filename}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Find and instantiate the PluginBase subclass
            # dir(module) - lists all names (attributes, functions, variables) inside that module
            # mro (method resolution order) - tuples for hierarchy of the inheritance
            plugin_instance = None

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                    issubclass(attr, PluginBase) and
                    attr is not PluginBase):
                    plugin_instance = attr()
                    break

            if plugin_instance is None:
                logger.error(f"No PluginBase subclass found in {plugin_filename}")
                return False

            # Initialize the plugin
            if not plugin_instance.initialize():
                logger.error(f"Plugin initialization failed: {plugin_instance.name}")
                return False

            # Store plugin and module
            plugin_key = plugin_filename.replace(".py", "")
            self.plugins[plugin_key] = plugin_instance
            self.plugins_modules[plugin_key] = module

            logger.info(f"Plugin loaded successfully: {plugin_instance.name}")
            return True

        except Exception as e:
            logger.error(f"Error loading plugin {plugin_filename}: {e}")
            return False

    def unload_plugin(self, plugin_key: str) -> bool:
        """
        Unload and cleanup a plugin

        Args:
            :param plugin_key: Key of the plugin to unload

        Returns:
            True if unloaded successfully, False otherwise
        """
        if plugin_key not in self.plugins:
            logger.warning(f"Plugin not found: {plugin_key}")
            return False

        try:
            plugin = self.plugins[plugin_key]

            # Shutdown the plugin
            if not plugin.shutdown():
                logger.warning(f"Plugin shutdown incomplete: {plugin.name}")

            # Remove from sys.modules
            if plugin_key in sys.modules:
                del sys.modules[plugin_key]

            # Remove from manager
            del self.plugins[plugin_key]
            del self.plugins_modules[plugin_key]

            logger.info(f"Plugin unloaded: {plugin.name}")
            return True

        except Exception as e:
            logger.error(f"Error unloading plugin {plugin_key}: {e}")
            return False

    def execute(self, plugin_key: str, *args, **kwargs) -> Any:
        """
        Execute a loaded plugin's main functionality.

        Args
            :param plugin_key: Key of the plugin to execute
            :param args, kwargs: Arguments to pass to plugin.execute()

        Returns:
            Result from plugin.execute() or None if plugin not found
        """

        if plugin_key not in self.plugins:
            logger.error(f"Plugin not found: {plugin_key}")
            return None

        try:
            plugin = self.plugins[plugin_key]

            if not plugin.enabled:
                logger.warning(f"Plugin is disabled: {plugin.name}")
                return None

            result = plugin.execute(*args, **kwargs)
            logger.info(f"Plugin executed successfully: {plugin.name}")
            return result

        except Exception as e:
            plugin = self.plugins.get(plugin_key)
            if plugin:
                plugin.on_error(e)
            logger.error(f"Error executing plugin {plugin_key}: {e}")
            return None

    def reload_plugin(self, plugin_key: str) -> bool:
        """Reload a plugin (unload and load again)."""
        if not self.unload_plugin(plugin_key):
            return False

        plugin_filename = f"{plugin_key}.py"
        return self.load_plugin(plugin_filename)

    def list_loaded_plugins(self) -> Dict[str, str]:
        """Return info about all loaded plugins."""
        return {
            key: plugin.name
            for key, plugin in self.plugins.items()
        }

    def enable_plugin(self, plugin_key: str) -> bool:
        """Enable a loaded plugin."""
        if plugin_key in self.plugins:
            self.plugins[plugin_key].enabled = True
            return True
        return False

    def disable_plugin(self, plugin_key: str) -> bool:
        """Disable a loaded plugin (won't execute)."""
        if plugin_key in self.plugins:
            self.plugins[plugin_key].enabled = False
            return True
        return False
