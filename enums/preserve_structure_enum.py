"""Enum for preserve_structure_strategies.py"""

from enum import Enum

class PreserveStructureType(Enum):
    """Enum for preserve structure strategies."""
    NONE = "PSNone"
    SOURCE_PARENT_REST = "PSSourceParentRest"
    FULL_SOURCE = "PSFullSource"

    @property
    def class_name(self) -> str:
        """Return the actual class name for instantiation."""
        return self.value

    def get_strategy_class(self):
        """Return the actual strategy class."""
        from preserve_structure_strategies import PSNone, PSSourceParentRest, PSFullSource

        strategy_map = {
            "PSNone": PSNone,
            "PSSourceParentRest": PSSourceParentRest,
            "PSFullSource": PSFullSource,
        }

        return strategy_map.get(self.value)

    def instantiate_strategy(self):
        """Instantiate and return the strategy object."""
        strategy_class = self.get_strategy_class()
        if strategy_class is None:
            raise ValueError(f"Unknown preserve structure type: {self.value}")
        return strategy_class()
