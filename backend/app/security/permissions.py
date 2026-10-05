"""Permission levels and the policy engine that gates every tool call."""

from enum import Enum, IntEnum


class PermissionLevel(IntEnum):
    PUBLIC_READ = 0   # read public information
    LOCAL_READ = 1    # read local files
    LOCAL_WRITE = 2   # create / modify files
    EXECUTE = 3       # run programs
    EXTERNAL = 4      # send external communications
    SENSITIVE = 5     # financial / security-sensitive actions


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"


class PolicyEngine:
    """Tools at or below `auto_approve_up_to` run automatically; anything higher needs the user."""

    def __init__(self, auto_approve_up_to: PermissionLevel = PermissionLevel.LOCAL_READ):
        # Never auto-approve external or sensitive actions, whatever the config says.
        self.auto_approve_up_to = min(auto_approve_up_to, PermissionLevel.EXECUTE)

    def evaluate(self, level: PermissionLevel) -> Decision:
        return Decision.ALLOW if level <= self.auto_approve_up_to else Decision.CONFIRM
