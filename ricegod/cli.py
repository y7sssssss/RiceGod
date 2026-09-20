"""Interactive graph demo with explicit resource ownership."""

import asyncio

from langchain_core.messages import HumanMessage

from ricegod.bootstrap import create_runtime


async def _interactive_main(runtime) -> None:
    """Run a small terminal loop for manually testing the graph."""
    print("RiceGod graph test. Type a question and press Enter.")
    print("Press Enter on an empty line, or type exit/quit/q to stop.")

    while True:
        config = {"configurable": {"thread_id": "terminal"}}
        question = input("\nQuestion> ").strip()
        if not question or question.lower() in {"exit", "quit", "q"}:
            break

        try:
            result = await runtime.graph.ainvoke({"messages": [HumanMessage(content=question)]}, config=config)
        except Exception as exc:
            print(f"Error: {exc}")
            continue

        router = result.get("router")
        if router:
            print("\nRoute summary:")
            print(router.model_dump_json(indent=2))

        messages = result.get("messages", [])
        if messages:
            print("\nAnswer:")
            print(messages[-1].content)


def main() -> None:
    runtime = create_runtime()
    try:
        asyncio.run(_interactive_main(runtime))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
