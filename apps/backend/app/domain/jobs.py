from __future__ import annotations

from enum import StrEnum


class TaskName(StrEnum):
    """Commands: work that should be done, executed by the Taskiq worker.

    Task names are the wire contract between the outbox dispatcher and the
    worker. Renaming one invalidates messages already queued in Redis, so treat
    these values as stable identifiers rather than internal labels.
    """

    ORDER_PROCESS = "order.process"
    ORDER_UPDATE_STATUS = "order.update_status"
    ORDER_CANCEL = "order.cancel"

    LINE_REPLY = "line.reply"
    LINE_PUSH = "line.push"

    AGENT_PROCESS = "agent.process"
