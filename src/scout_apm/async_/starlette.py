# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import wrapt
from starlette.background import BackgroundTask
from starlette.requests import Request

import scout_apm.core
from scout_apm.core.tracked_request import TrackedRequest
from scout_apm.core.web_requests import (
    create_filtered_path,
    ignore_path,
    track_amazon_request_queue_time,
    track_request_queue_time,
)


class ScoutMiddleware:
    def __init__(self, app):
        self.app = app
        installed = scout_apm.core.install()
        self._do_nothing = not installed
        if installed:
            install_background_instrumentation()

    async def __call__(self, scope, receive, send):
        if self._do_nothing or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        tracked_request = TrackedRequest.instance()
        # Can't name controller until post-routing - see final clause
        controller_span = tracked_request.start_span(operation="Controller/Unknown")

        tracked_request.tag(
            "path",
            create_filtered_path(request.url.path, request.query_params.multi_items()),
        )
        if ignore_path(request.url.path):
            tracked_request.tag("ignore_transaction", True)

        user_ip = (
            request.headers.get("x-forwarded-for", default="").split(",")[0]
            or request.headers.get("client-ip", default="").split(",")[0]
            or request.client.host
        )
        tracked_request.tag("user_ip", user_ip)

        queue_time = request.headers.get(
            "x-queue-start", default=""
        ) or request.headers.get("x-request-start", default="")
        tracked_queue_time = track_request_queue_time(queue_time, tracked_request)
        if not tracked_queue_time:
            amazon_queue_time = request.headers.get("x-amzn-trace-id", default="")
            track_amazon_request_queue_time(amazon_queue_time, tracked_request)

        def rename_controller_span_from_endpoint():
            if "endpoint" in scope:
                # Rename top span
                endpoint = scope["endpoint"]
                controller_span.operation = "Controller/{}.{}".format(
                    endpoint.__module__, endpoint.__qualname__
                )
                tracked_request.is_real_request = True

        async def wrapped_send(data):
            # Finish HTTP span when body finishes sending, not later (e.g.
            # after background tasks)
            if data.get("type", None) == "http.response.body" and not data.get(
                "more_body", False
            ):
                rename_controller_span_from_endpoint()
                tracked_request.stop_span()
            return await send(data)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            tracked_request.tag("error", "true")
            raise exc
        finally:
            if tracked_request.end_time is None:
                rename_controller_span_from_endpoint()
                tracked_request.stop_span()


background_instrumentation_installed = False


def install_background_instrumentation():
    global background_instrumentation_installed
    if background_instrumentation_installed:
        return
    background_instrumentation_installed = True

    @wrapt.decorator
    async def wrapped_background_call(wrapped, instance, args, kwargs):
        tracked_request = TrackedRequest.instance()
        tracked_request.is_real_request = True
        tracked_request.start_span(
            operation="Job/{}.{}".format(
                instance.func.__module__, instance.func.__qualname__
            )
        )
        try:
            return await wrapped(*args, **kwargs)
        finally:
            tracked_request.stop_span()

    BackgroundTask.__call__ = wrapped_background_call(BackgroundTask.__call__)
