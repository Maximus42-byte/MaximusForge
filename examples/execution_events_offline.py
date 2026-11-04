"""Collect a retry timeline from an offline workflow."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from maximusforge.events import InMemoryEventCollector
from maximusforge.workflows import (
    END,
    ExecutionConfig,
    FunctionNode,
    NodeExecutionPolicy,
    RetryPolicy,
    Workflow,
)
from maximusforge.workflows.engines.langgraph_engine import LangGraphEngine


class State(BaseModel):
    value: int = 0


class TransientError(Exception):
    pass


async def main() -> None:
    attempts = 0

    async def work(state: State) -> State:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientError("temporary offline failure")
        return state.model_copy(update={"value": state.value + 1})

    policy = NodeExecutionPolicy(
        retry_policy=RetryPolicy(max_attempts=2, retry_on=(TransientError,), initial_delay=0)
    )
    workflow = (
        Workflow("offline-events", State)
        .add_node(FunctionNode("work", work, policy=policy))
        .set_entry("work")
        .add_edge("work", END)
    )
    collector = InMemoryEventCollector()
    result = await LangGraphEngine().execute(
        workflow, State(), config=ExecutionConfig(event_sink=collector)
    )
    print(f"run: {result.run_id}")
    for event in collector.events:
        detail = f" node={event.node_name}" if event.node_name is not None else ""
        if event.attempt is not None:
            detail += f" attempt={event.attempt}"
        if event.category is not None:
            detail += f" category={event.category.value}"
        print(f"{event.sequence:02d} {event.type.value}{detail}")
    print(f"final value: {result.state.value}")


if __name__ == "__main__":
    asyncio.run(main())
