"""Run a retrying node and recovery route entirely offline."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from maximusforge.core import NodeFailure
from maximusforge.workflows import (
    END,
    FunctionNode,
    FunctionRecoveryNode,
    NodeExecutionPolicy,
    RetryPolicy,
    Workflow,
)
from maximusforge.workflows.engines.langgraph_engine import LangGraphEngine


class State(BaseModel):
    outcome: str = "pending"


class TemporaryFailure(Exception):
    pass


async def work(state: State) -> State:
    raise TemporaryFailure("offline demo failure")


async def recover(state: State, failure: NodeFailure) -> State:
    return State(outcome=f"recovered after {failure.attempts} attempts")


async def main() -> None:
    policy = NodeExecutionPolicy(
        timeout_seconds=1.0,
        retry_policy=RetryPolicy(
            max_attempts=2, retry_on=(TemporaryFailure,), initial_delay=0
        ),
    )
    workflow = (
        Workflow("reliability-demo", State)
        .add_node(FunctionNode("work", work, policy=policy))
        .add_node(FunctionRecoveryNode("recover", recover))
        .set_entry("work")
        .add_edge("work", END)
        .add_error_edge("work", "recover")
        .add_edge("recover", END)
    )
    result = await LangGraphEngine().execute(workflow, State())
    print(result.state.outcome)
    print("trace:", " -> ".join(result.trace))
    print("attempts:", result.records[0].attempts)
    print("recovery route:", result.records[0].routed_to)


if __name__ == "__main__":
    asyncio.run(main())
