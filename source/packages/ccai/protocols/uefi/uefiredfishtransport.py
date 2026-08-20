"""
    .. module:: uefiredfishtransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing :class:`UefiRedfishTransport`, a layer-2
               HTTPS transport that reaches a UEFI target via the DMTF
               Redfish REST API exposed by the target's BMC or out-of-band
               management controller.

               The transport implements :class:`IUefiTransport` from
               :mod:`ccai.protocols.uefi.uefiinterfaces`, consumes
               :class:`UefiCmdAspects` from
               :mod:`ccai.protocols.uefi.uefiaspects`, and owns a
               single :class:`requests.Session` for connection-pool reuse.
               It handles token and basic authentication (auto-renewing
               tokens on ``401``), defaults TLS verification to ``True``,
               polls ``202 Accepted`` TaskMonitor endpoints per DSP0266,
               and caches response bodies briefly when an ``ETag`` is
               returned.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import collections
import os
import time

from typing import Any, BinaryIO, Dict, Optional, Tuple

import requests

from ccai.errors.exceptions import ConfigurationError, SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefilogging import UEFI_REDFISH_CHANNEL, get_uefi_logger
from ccai.protocols.uefi.uefiredfishcredential import UefiRedfishCredential


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDFISH_SERVICE_ROOT_PATH: str = "/redfish/v1/"
REDFISH_SESSION_COLLECTION_PATH: str = "/redfish/v1/SessionService/Sessions"

MANDATORY_HEADERS: Dict[str, str] = {
    "OData-Version": "4.0",
    "Accept": "application/json",
}

JSON_CONTENT_TYPE: str = "application/json"

TERMINAL_TASK_STATES: Tuple[str, ...] = (
    "Completed",
    "Exception",
    "Killed",
    "Cancelled",
    "Suspended",
    "Interrupted",
)

TERMINAL_FAILURE_TASK_STATES: Tuple[str, ...] = (
    "Exception",
    "Killed",
    "Cancelled",
)

DEFAULT_ETAG_CACHE_MAX_ENTRIES: int = 256

REDFISH_TRANSPORT_KIND: str = "redfish"


# ---------------------------------------------------------------------------
# UefiRedfishTransport
# ---------------------------------------------------------------------------

class UefiRedfishTransport:
    """
        Layer-2 Redfish-over-HTTPS transport.  Implements the
        :class:`IUefiTransport` structural contract.  Owns a single
        :class:`requests.Session` for the TCP connection-pool, the
        authentication state (token, session URI), and the ETag response
        cache.  Retry semantics are driven entirely by the caller-supplied
        :class:`UefiCmdAspects` — no behavior-modifying decorators are used.

        The transport is not a byte pipe.  :meth:`send`, :meth:`recv`, and
        :meth:`recv_until` raise :class:`SemanticError` because REST calls
        are the correct interaction model.

        :param credential: The :class:`UefiRedfishCredential` carrying the
                           base URL, verify flag, and either a username +
                           password pair or a pre-issued token.
        :param aspects: Default :class:`UefiCmdAspects` used when a caller
                        passes ``aspects=None`` on a per-verb call.  Must
                        not be ``None``; defaults to
                        :data:`DEFAULT_CMD_ASPECTS`.
    """

    def __init__(self, credential: UefiRedfishCredential,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize every instance variable.  No network I/O is issued
            here; call :meth:`connect` to open the underlying session.

            :param credential: The Redfish credential bundle.
            :param aspects: The default :class:`UefiCmdAspects` for this
                            instance.

            :raises: ConfigurationError
        """
        if not isinstance(credential, UefiRedfishCredential):
            err_msg_lines = [
                "'credential' must be a UefiRedfishCredential instance.",
                "GOT: value={!r} type={}".format(credential, type(credential).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if aspects is None:
            err_msg_lines = [
                "'aspects' must be a UefiCmdAspects instance.",
                "A None default aspects makes aspects=None on per-verb calls unresolvable.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        # Derived connection state
        self._credential: UefiRedfishCredential = credential
        self._aspects: UefiCmdAspects = aspects
        self._base_url: str = credential.base_url.rstrip("/")
        self._verify: Any = self._resolve_verify_setting(credential=credential)

        # Runtime-owned resources
        self._session: Optional[requests.Session] = None
        self._connected: bool = False

        # Authentication state
        self._auth_token: Optional[str] = None
        self._session_uri: Optional[str] = None
        self._session_created_by_us: bool = False

        # ETag response cache (LRU via OrderedDict)
        self._etag_cache: "collections.OrderedDict[str, Tuple[str, Dict[str, Any]]]" = collections.OrderedDict()
        self._etag_cache_max_entries: int = DEFAULT_ETAG_CACHE_MAX_ENTRIES

        # Logger
        self._logger = get_uefi_logger(UEFI_REDFISH_CHANNEL)

        return

    # ------------------------------------------------------------------
    # IUefiTransport surface
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the transport has an open, usable
                      session to the target BMC / Redfish service.
        """
        rtnval = self._connected
        return rtnval

    @property
    def transport_kind(self) -> str:
        """
            :returns: The string ``"redfish"``.
        """
        rtnval = REDFISH_TRANSPORT_KIND
        return rtnval

    def connect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Open the underlying :class:`requests.Session` and authenticate.

            The retry loop honors ``aspects.connection_timeout`` and
            ``aspects.connection_interval``.  When the ``action_pattern`` is
            :data:`ActionPattern.SINGLE_CONNECTED_CALL` and the transport is
            already connected, the method is a no-op.
            :data:`ActionPattern.DO_WHILE_SUCCESS` is not meaningful for
            ``connect()`` and raises :class:`SemanticError`.

            :param aspects: Optional per-call aspects overriding the instance
                            default.

            :raises: SemanticError, ConnectionError, UefiTransportError,
                     TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if effective.action_pattern == ActionPattern.DO_WHILE_SUCCESS:
            err_msg_lines = [
                "ActionPattern.DO_WHILE_SUCCESS is not meaningful for connect().",
                "BASE_URL: {}".format(self._base_url),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        if effective.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL and self._connected:
            return

        deadline = time.monotonic() + float(effective.connection_timeout)
        allowed_exceptions = tuple(effective.allowed_connection_exceptions)
        attempt_count = 0
        last_exc: Optional[BaseException] = None

        while True:
            attempt_count += 1
            try:
                self._open_session_and_authenticate()
                self._connected = True
                return
            except Exception as xcpt:
                last_exc = xcpt

                if len(allowed_exceptions) > 0 and not isinstance(xcpt, allowed_exceptions):
                    raise

                if effective.must_connect:
                    err_msg_lines = [
                        "Redfish connect() failed and must_connect=True.",
                        "BASE_URL: {}".format(self._base_url),
                        "ATTEMPTS: {}".format(attempt_count),
                        "LAST EXC: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise ConnectionError(err_msg) from xcpt

                if effective.action_pattern == ActionPattern.SINGLE_CALL:
                    raise

                now_mono = time.monotonic()
                if now_mono >= deadline:
                    elapsed = float(effective.connection_timeout)
                    err_msg_lines = [
                        "Redfish connect() exceeded connection_timeout.",
                        "BASE_URL: {}".format(self._base_url),
                        "ATTEMPTS: {}".format(attempt_count),
                        "ELAPSED:  {:.2f}s".format(elapsed),
                        "LAST EXC: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise TimeoutError(err_msg) from last_exc

            time.sleep(float(effective.connection_interval))

        # Unreachable; present for static analyzers.
        return

    def disconnect(self) -> None:
        """
            Tear down the underlying :class:`requests.Session`.  When the
            transport created a Redfish session resource (via
            ``POST /SessionService/Sessions``), this method DELETEs that
            resource before closing the HTTP session.  Safe to call twice.
        """
        if self._session is None:
            self._connected = False
            return

        # DELETE the Redfish session resource when we own it.
        if self._session_created_by_us and self._session_uri is not None:
            delete_url = self._resolve_url(path=self._session_uri)
            try:
                headers = self._build_headers(extra=None, has_body=False)
                self._session.delete(delete_url, headers=headers, verify=self._verify)
            except Exception as xcpt:
                self._logger.warning(
                    "Redfish session DELETE failed during disconnect: %s: %s",
                    type(xcpt).__name__, xcpt,
                )

        try:
            self._session.close()
        except Exception as xcpt:
            self._logger.warning(
                "Redfish session close() raised during disconnect: %s: %s",
                type(xcpt).__name__, xcpt,
            )

        # Reset every piece of runtime state.
        self._session = None
        self._auth_token = None
        self._session_uri = None
        self._session_created_by_us = False
        self._connected = False
        self._etag_cache.clear()

        return

    def send(self, data: bytes) -> int:
        """
            Redfish is not a byte-pipe transport; this method always raises
            :class:`SemanticError`.

            :param data: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishTransport.send() is not a meaningful operation.",
            "Redfish is a REST transport, not a byte pipe.  Use get/post/patch/delete/upload.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Redfish is not a byte-pipe transport; this method always raises
            :class:`SemanticError`.

            :param max_bytes: Ignored.
            :param timeout: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishTransport.recv() is not a meaningful operation.",
            "Redfish is a REST transport, not a byte pipe.  Use get/post/patch/delete/upload.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv_until(self, sentinel: bytes, timeout: float) -> bytes:
        """
            Redfish is not a byte-pipe transport; this method always raises
            :class:`SemanticError`.

            :param sentinel: Ignored.
            :param timeout: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishTransport.recv_until() is not a meaningful operation.",
            "Redfish is a REST transport, not a byte pipe.  Use get/post/patch/delete/upload.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    # ------------------------------------------------------------------
    # REST verbs
    # ------------------------------------------------------------------

    def get(self, path: str, aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Issue a GET request.  Handles ETag caching: on a cached URL the
            request carries ``If-None-Match: <etag>``; on a 304 response the
            cached body is returned verbatim.  Handles 401 auto-renewal and
            ``202 Accepted`` TaskMonitor polling.

            :param path: The Redfish resource path (absolute starting with
                         ``/`` or a full URL).
            :param aspects: Optional per-call aspects.

            :returns: The JSON-decoded response body as a dict.
            :raises: SemanticError, UefiTransportError, TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self._require_connected(method_name="get")

        url = self._resolve_url(path=path)
        rtnval = self._execute_request(
            method="GET",
            url=url,
            body=None,
            files=None,
            effective=effective,
        )
        return rtnval

    def post(self, path: str, body: Dict[str, Any],
                   aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Issue a POST request.  Handles 401 auto-renewal and
            ``202 Accepted`` TaskMonitor polling.  Evicts any cached ETag
            entry for the target URL before sending.

            :param path: The Redfish resource path.
            :param body: The JSON body dict (may be an empty dict).
            :param aspects: Optional per-call aspects.

            :returns: The JSON-decoded response body as a dict.
            :raises: SemanticError, UefiTransportError, TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self._require_connected(method_name="post")

        url = self._resolve_url(path=path)
        self._evict_etag(url=url)

        rtnval = self._execute_request(
            method="POST",
            url=url,
            body=body,
            files=None,
            effective=effective,
        )
        return rtnval

    def patch(self, path: str, body: Dict[str, Any],
                    aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Issue a PATCH request.  Handles 401 auto-renewal and
            ``202 Accepted`` TaskMonitor polling.  Evicts any cached ETag
            entry for the target URL before sending.

            :param path: The Redfish resource path.
            :param body: The JSON body dict.
            :param aspects: Optional per-call aspects.

            :returns: The JSON-decoded response body as a dict.
            :raises: SemanticError, UefiTransportError, TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self._require_connected(method_name="patch")

        url = self._resolve_url(path=path)
        self._evict_etag(url=url)

        rtnval = self._execute_request(
            method="PATCH",
            url=url,
            body=body,
            files=None,
            effective=effective,
        )
        return rtnval

    def delete(self, path: str,
                     aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Issue a DELETE request.  Handles 401 auto-renewal and
            ``202 Accepted`` TaskMonitor polling.  Evicts any cached ETag
            entry for the target URL before sending.

            :param path: The Redfish resource path.
            :param aspects: Optional per-call aspects.

            :returns: The JSON-decoded response body as a dict, or an empty
                      dict when the server returns no body.
            :raises: SemanticError, UefiTransportError, TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self._require_connected(method_name="delete")

        url = self._resolve_url(path=path)
        self._evict_etag(url=url)

        rtnval = self._execute_request(
            method="DELETE",
            url=url,
            body=None,
            files=None,
            effective=effective,
        )
        return rtnval

    def upload(self, path: str, file_obj: BinaryIO,
                     aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Issue a multipart/form-data POST suitable for Redfish capsule
            upload.  Streams ``file_obj`` from disk rather than loading the
            full payload into memory.  Handles 401 auto-renewal and
            ``202 Accepted`` TaskMonitor polling.

            :param path: The Redfish upload endpoint path.
            :param file_obj: A binary file-like object opened for reading.
            :param aspects: Optional per-call aspects.

            :returns: The JSON-decoded response body as a dict.
            :raises: SemanticError, UefiTransportError, TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self._require_connected(method_name="upload")

        url = self._resolve_url(path=path)
        self._evict_etag(url=url)

        files_payload: Dict[str, Any] = {
            "UpdateFile": ("capsule.bin", file_obj, "application/octet-stream"),
        }

        rtnval = self._execute_request(
            method="POST",
            url=url,
            body=None,
            files=files_payload,
            effective=effective,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_verify_setting(self, credential: UefiRedfishCredential) -> Any:
        """
            Translate the credential's TLS flags into the ``verify`` value
            that :class:`requests.Session` accepts (``True``, ``False``, or
            a path to a CA bundle).

            :param credential: The credential bundle.

            :returns: The resolved ``verify`` value.
        """
        if credential.ca_bundle is not None:
            rtnval: Any = credential.ca_bundle
        elif credential.verify_tls is True:
            rtnval = True
        else:
            rtnval = False

        return rtnval

    def _open_session_and_authenticate(self) -> None:
        """
            Build a new :class:`requests.Session`, populate default headers,
            and authenticate per the credential.  When the credential carries
            a pre-issued token the transport attaches it directly; otherwise
            a ``POST /SessionService/Sessions`` obtains a fresh token.

            :raises: UefiTransportError
        """
        new_session = requests.Session()
        new_session.verify = self._verify
        new_session.headers.update(MANDATORY_HEADERS)

        self._session = new_session

        if self._credential.token is not None:
            self._auth_token = self._credential.token
            self._session_created_by_us = False
            self._session_uri = None
            self._session.headers.update({"X-Auth-Token": self._auth_token})
            return

        # Basic-style session bootstrap: POST to the Sessions collection.
        self._perform_session_login()

        return

    def _perform_session_login(self) -> None:
        """
            POST to ``/redfish/v1/SessionService/Sessions`` using the
            credential's username and password, capture the ``X-Auth-Token``
            response header, and attach it on the session object.

            :raises: UefiTransportError
        """
        if self._session is None:
            err_msg = "Internal error: _perform_session_login called without an active requests.Session."
            raise UefiTransportError(err_msg) from None

        if self._credential.username is None or self._credential.password is None:
            err_msg_lines = [
                "Redfish session authentication requires username and password.",
                "BASE_URL: {}".format(self._base_url),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        login_url = self._resolve_url(path=REDFISH_SESSION_COLLECTION_PATH)
        body: Dict[str, str] = {
            "UserName": self._credential.username,
            "Password": self._credential.password,
        }
        headers = self._build_headers(extra=None, has_body=True)

        response = self._session.post(
            login_url,
            json=body,
            headers=headers,
            verify=self._verify,
        )

        status_code = response.status_code
        if status_code < 200 or status_code >= 300:
            err_msg_lines = [
                "Redfish session login failed.",
                "URL:    {}".format(login_url),
                "STATUS: {}".format(status_code),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            details = [
                "method=POST",
                "status={}".format(status_code),
                "url={}".format(login_url),
            ]
            raise UefiTransportError(err_msg, details=details) from None

        token_value = response.headers.get("X-Auth-Token")
        if token_value is None or len(token_value) == 0:
            err_msg_lines = [
                "Redfish session login returned no X-Auth-Token header.",
                "URL:    {}".format(login_url),
                "STATUS: {}".format(status_code),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        session_uri = response.headers.get("Location")

        self._auth_token = token_value
        self._session_uri = session_uri
        self._session_created_by_us = True
        self._session.headers.update({"X-Auth-Token": self._auth_token})

        return

    def _build_headers(self, extra: Optional[Dict[str, str]],
                             has_body: bool) -> Dict[str, str]:
        """
            Assemble the outbound header set.  Mandatory headers always
            win on key collision with caller-supplied extras.

            :param extra: Optional caller-supplied extra headers.
            :param has_body: When ``True`` the ``Content-Type: application/json``
                             header is added.

            :returns: A new dict of headers to attach to a single request.
        """
        headers: Dict[str, str] = {}
        if extra is not None:
            headers.update(extra)

        # Mandatory headers win on collision.
        headers.update(MANDATORY_HEADERS)

        if has_body:
            headers["Content-Type"] = JSON_CONTENT_TYPE

        if self._auth_token is not None:
            headers["X-Auth-Token"] = self._auth_token

        return headers

    def _resolve_url(self, path: str) -> str:
        """
            Combine the base URL and a relative path into an absolute URL.
            Absolute URLs are returned unchanged.  Leading ``/`` is honored.

            :param path: Absolute URL or server-relative path.

            :returns: An absolute URL.
        """
        if path.startswith("http://") or path.startswith("https://"):
            rtnval = path
        elif path.startswith("/"):
            rtnval = self._base_url + path
        else:
            rtnval = self._base_url + "/" + path

        return rtnval

    def _require_connected(self, method_name: str) -> None:
        """
            Guard against using a verb on a transport that has never been
            connected or that has been :meth:`disconnect`-ed.

            :param method_name: The name of the verb being protected.

            :raises: SemanticError
        """
        if not self._connected or self._session is None:
            err_msg_lines = [
                "UefiRedfishTransport.{}() called on a disconnected transport.".format(method_name),
                "BASE_URL: {}".format(self._base_url),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        return

    def _execute_request(self, method: str, url: str,
                               body: Optional[Dict[str, Any]],
                               files: Optional[Dict[str, Any]],
                               effective: UefiCmdAspects) -> Dict[str, Any]:
        """
            Perform a single request with 401 auto-renewal (bounded to one
            retry) and TaskMonitor polling on ``202 Accepted``.  ETag
            caching is applied to GET only.

            :param method: HTTP method verb.
            :param url: Absolute URL.
            :param body: Optional JSON body.
            :param files: Optional multipart files payload.
            :param effective: The resolved :class:`UefiCmdAspects`.

            :returns: The decoded response body, or the cached body on 304.
            :raises: UefiTransportError, TimeoutError
        """
        response = self._send_once(method=method, url=url, body=body, files=files)

        if response.status_code == 401 and self._can_renew_token():
            self._logger.warning(
                "Received 401 on %s %s; attempting token renewal.", method, url,
            )
            self._renew_token()
            response = self._send_once(method=method, url=url, body=body, files=files)

        if response.status_code == 304 and method == "GET":
            cached = self._read_etag_cache(url=url)
            if cached is not None:
                cached_etag, cached_body = cached
                return cached_body

            err_msg_lines = [
                "Received 304 Not Modified but no cached body is available.",
                "URL: {}".format(url),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        status_code = response.status_code

        if status_code == 202:
            monitor_url = response.headers.get("Location")
            if monitor_url is None or len(monitor_url) == 0:
                err_msg_lines = [
                    "Received 202 Accepted without a Location header.",
                    "METHOD: {}".format(method),
                    "URL:    {}".format(url),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiTransportError(err_msg) from None

            rtnval = self._poll_task_monitor(monitor_url=monitor_url, effective=effective)
            return rtnval

        if status_code < 200 or status_code >= 300:
            body_excerpt = self._safe_body_excerpt(response=response)
            err_msg_lines = [
                "Redfish request returned a non-success status.",
                "METHOD: {}".format(method),
                "URL:    {}".format(url),
                "STATUS: {}".format(status_code),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            details = [
                "method={}".format(method),
                "url={}".format(url),
                "status={}".format(status_code),
                "body_excerpt={}".format(body_excerpt),
            ]
            raise UefiTransportError(err_msg, details=details) from None

        if method == "GET":
            self._record_etag(url=url, response=response)

        rtnval = self._decode_json_body(response=response)
        return rtnval

    def _send_once(self, method: str, url: str,
                         body: Optional[Dict[str, Any]],
                         files: Optional[Dict[str, Any]]) -> requests.Response:
        """
            Emit a single HTTP request with all mandatory headers attached.
            GET requests include ``If-None-Match`` when a cached ETag is
            present for the URL.

            :param method: HTTP method verb.
            :param url: Absolute URL.
            :param body: Optional JSON body.
            :param files: Optional multipart files payload.

            :returns: The :class:`requests.Response` object.
        """
        if self._session is None:
            err_msg = "Internal error: _send_once called with no active requests.Session."
            raise UefiTransportError(err_msg) from None

        extra: Optional[Dict[str, str]] = None
        if method == "GET":
            cached = self._read_etag_cache(url=url)
            if cached is not None:
                cached_etag, _cached_body = cached
                extra = {"If-None-Match": cached_etag}

        has_body = body is not None
        # Multipart files payload omits Content-Type here; requests sets a boundary.
        if files is not None:
            has_body = False

        headers = self._build_headers(extra=extra, has_body=has_body)

        request_kwargs: Dict[str, Any] = {
            "headers": headers,
            "verify": self._verify,
        }
        if body is not None and files is None:
            request_kwargs["json"] = body
        if files is not None:
            request_kwargs["files"] = files

        method_upper = method.upper()
        if method_upper == "GET":
            response = self._session.get(url, **request_kwargs)
        elif method_upper == "POST":
            response = self._session.post(url, **request_kwargs)
        elif method_upper == "PATCH":
            response = self._session.patch(url, **request_kwargs)
        elif method_upper == "DELETE":
            response = self._session.delete(url, **request_kwargs)
        else:
            err_msg_lines = [
                "Internal error: _send_once received an unsupported method.",
                "METHOD: {}".format(method),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        return response

    def _can_renew_token(self) -> bool:
        """
            Determine whether the transport has credentials to obtain a
            fresh session token on 401.

            :returns: ``True`` when username+password are available.
        """
        can_renew = (
            self._credential.username is not None
            and self._credential.password is not None
        )
        return can_renew

    def _renew_token(self) -> None:
        """
            Re-issue the session login POST to obtain a fresh
            ``X-Auth-Token``.  Raises :class:`UefiTransportError` when the
            second login itself fails.

            :raises: UefiTransportError
        """
        # Clear stale token before re-login to avoid the stale value winning
        # a merge on the next request.
        self._auth_token = None
        if self._session is not None:
            if "X-Auth-Token" in self._session.headers:
                del self._session.headers["X-Auth-Token"]

        self._perform_session_login()
        return

    def _poll_task_monitor(self, monitor_url: str,
                                 effective: UefiCmdAspects) -> Dict[str, Any]:
        """
            Poll a TaskMonitor URL until a terminal ``TaskState`` is
            observed or ``aspects.completion_timeout`` elapses.

            :param monitor_url: TaskMonitor URL (from the 202 ``Location``).
            :param effective: The resolved aspects driving the polling.

            :returns: The terminal task body on ``Completed`` / 200.
            :raises: UefiTransportError, TimeoutError
        """
        full_monitor_url = self._resolve_url(path=monitor_url)
        deadline = time.monotonic() + float(effective.completion_timeout)
        last_state: Optional[str] = None
        poll_count = 0

        while True:
            poll_count += 1
            response = self._send_once(
                method="GET",
                url=full_monitor_url,
                body=None,
                files=None,
            )
            status_code = response.status_code

            # A terminal 2xx response with a parsable body may still be the task body
            # with a TaskState.  If the server has transitioned to the final resource
            # body (200 + non-task payload) we stop.
            body = self._decode_json_body(response=response)

            state_value = body.get("TaskState") if isinstance(body, dict) else None
            if isinstance(state_value, str):
                last_state = state_value

                if state_value == "Completed":
                    return body

                if state_value in TERMINAL_FAILURE_TASK_STATES:
                    messages = body.get("Messages", []) if isinstance(body, dict) else []
                    err_msg_lines = [
                        "Redfish Task terminated in failure state.",
                        "MONITOR: {}".format(full_monitor_url),
                        "STATE:   {}".format(state_value),
                        "POLLS:   {}".format(poll_count),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    details = [
                        "monitor_url={}".format(full_monitor_url),
                        "task_state={}".format(state_value),
                        "messages={!r}".format(messages),
                    ]
                    raise UefiTransportError(err_msg, details=details) from None

                if state_value in TERMINAL_TASK_STATES:
                    # Suspended / Interrupted are terminal-non-failure states.
                    return body

            else:
                # No TaskState in body — if status is 2xx treat it as terminal success.
                if 200 <= status_code < 300 and status_code != 202:
                    return body

            if time.monotonic() >= deadline:
                elapsed = float(effective.completion_timeout)
                err_msg_lines = [
                    "Redfish TaskMonitor polling exceeded completion_timeout.",
                    "MONITOR:    {}".format(full_monitor_url),
                    "LAST STATE: {}".format(last_state),
                    "ELAPSED:    {:.2f}s".format(elapsed),
                    "POLLS:      {}".format(poll_count),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise TimeoutError(err_msg) from None

            time.sleep(float(effective.completion_interval))

        # Unreachable; present for static analyzers.
        return {}

    def _decode_json_body(self, response: requests.Response) -> Dict[str, Any]:
        """
            Decode a JSON response body, tolerating empty / non-JSON bodies
            by returning an empty dict.

            :param response: The :class:`requests.Response` to decode.

            :returns: The decoded dict, or an empty dict when no JSON body
                      is present.
        """
        try:
            decoded = response.json()
        except ValueError:
            rtnval: Dict[str, Any] = {}
            return rtnval

        if isinstance(decoded, dict):
            rtnval = decoded
        else:
            rtnval = {"value": decoded}

        return rtnval

    def _safe_body_excerpt(self, response: requests.Response) -> str:
        """
            Render a short printable excerpt of a failing response body for
            inclusion in exception ``details``.

            :param response: The :class:`requests.Response`.

            :returns: A short string; never longer than 400 characters.
        """
        raw_text: str
        try:
            raw_text = response.text if response.text is not None else ""
        except Exception:
            raw_text = ""

        if len(raw_text) > 400:
            rtnval = raw_text[:400] + "... (truncated)"
        else:
            rtnval = raw_text

        return rtnval

    # ------------------------------------------------------------------
    # ETag cache
    # ------------------------------------------------------------------

    def _read_etag_cache(self, url: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
            Read the cached ETag + body tuple for ``url`` and mark it as
            recently-used.

            :param url: Absolute request URL.

            :returns: A ``(etag, body)`` tuple or ``None`` on a miss.
        """
        if url not in self._etag_cache:
            rtnval: Optional[Tuple[str, Dict[str, Any]]] = None
            return rtnval

        self._etag_cache.move_to_end(url)
        rtnval = self._etag_cache[url]
        return rtnval

    def _record_etag(self, url: str, response: requests.Response) -> None:
        """
            Cache the response body keyed by URL if the response carried an
            ``ETag`` header.  Enforces the LRU cap.

            :param url: Absolute request URL.
            :param response: The :class:`requests.Response` that carried the
                             body.
        """
        etag_value = response.headers.get("ETag")
        if etag_value is None or len(etag_value) == 0:
            return

        body = self._decode_json_body(response=response)
        self._etag_cache[url] = (etag_value, body)
        self._etag_cache.move_to_end(url)

        while len(self._etag_cache) > self._etag_cache_max_entries:
            self._etag_cache.popitem(last=False)

        return

    def _evict_etag(self, url: str) -> None:
        """
            Remove any cached ETag entry keyed by ``url``.  Called by
            non-GET verbs before dispatching.

            :param url: Absolute request URL.
        """
        if url in self._etag_cache:
            del self._etag_cache[url]

        return

    # ------------------------------------------------------------------
    # Context manager sugar
    # ------------------------------------------------------------------

    def __enter__(self) -> "UefiRedfishTransport":
        """
            Enter the context manager; open the session via :meth:`connect`.

            :returns: ``self``.
        """
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
            Exit the context manager; tear down via :meth:`disconnect`.
        """
        self.disconnect()
        return


__all__ = [
    "UefiRedfishTransport",
    "MANDATORY_HEADERS",
    "REDFISH_SERVICE_ROOT_PATH",
    "REDFISH_SESSION_COLLECTION_PATH",
    "REDFISH_TRANSPORT_KIND",
    "TERMINAL_TASK_STATES",
    "TERMINAL_FAILURE_TASK_STATES",
    "DEFAULT_ETAG_CACHE_MAX_ENTRIES",
]
