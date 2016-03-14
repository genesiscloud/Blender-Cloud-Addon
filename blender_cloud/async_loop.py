"""Manages the asyncio loop."""

import asyncio
import traceback

import bpy


def kick_async_loop(*args):
    loop = asyncio.get_event_loop()

    if loop.is_closed():
        print('{}: loop closed, stopping'.format(__name__))
        stop_async_loop()
        return

    all_tasks = asyncio.Task.all_tasks()
    if not all_tasks:
        print('{}: no more scheduled tasks, stopping'.format(__name__))
        stop_async_loop()
        return

    if all(task.done() for task in all_tasks):
        print('{}: all tasks are done, fetching results and stopping.'.format(__name__))
        for task in all_tasks:
            # noinspection PyBroadException
            try:
                task.result()
            except asyncio.CancelledError:
                # No problem, we want to stop anyway.
                pass
            except Exception:
                print('{}: resulted in exception'.format(task))
                traceback.print_exc()
        stop_async_loop()
        return

    # Perform a single async loop step
    async def do_nothing():
        pass

    loop.run_until_complete(do_nothing())


def async_loop_handler() -> callable:
    name = kick_async_loop.__name__
    for handler in bpy.app.handlers.scene_update_pre:
        if getattr(handler, '__name__', '') == name:
            return handler
    return None


def ensure_async_loop():
    if async_loop_handler() is not None:
        return
    bpy.app.handlers.scene_update_pre.append(kick_async_loop)


def stop_async_loop():
    handler = async_loop_handler()
    if handler is None:
        return
    bpy.app.handlers.scene_update_pre.remove(handler)
