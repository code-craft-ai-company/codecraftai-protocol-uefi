"""
    .. module:: test_uefiredfishtransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiRedfishTransport`.  Drives the
               transport against a stubbed :class:`requests.Session` built
               with :class:`unittest.mock.MagicMock`; exercises auth flows,
               401 auto-renewal, ``202 Accepted`` TaskMonitor polling,
               ``ETag`` caching, disconnect teardown, and TLS verify
               defaults.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import json
import unittest

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from ccai.errors.exceptions import SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefiredfishcredential import UefiRedfishCredential
from ccai.protocols.uefi.uefiredfishtransport import (
    MANDATORY_HEADERS,
    REDFISH_SESSION_COLLECTION_PATH,
    REDFISH_TRANSPORT_KIND,
    UefiRedfishTransport,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int,
                   headers: Optional[Dict[str, str]] = None,
                   body: Optional[Any] = None) -> MagicMock:
    """
        Build a :class:`MagicMock` shaped like a :class:`requests.Response`.

        :param status_code: The HTTP status code.
        :param headers: Optional response headers dict.
        :param body: Optional body — dict / list stored as ``response.json()``,
                     string stored as ``response.text``.

        :returns: The configured mock response.
    """
    resolved_headers: Dict[str, str] = {} if headers is None else dict(headers)

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = resolved_headers

    if body is None:
        mock_response.json.side_effect = ValueError("no body")
        mock_response.text = ""
    elif isinstance(body, (dict, list)):
        mock_response.json.return_value = body
        mock_response.text = json.dumps(body)
    else:
        mock_response.json.side_effect = ValueError("non-json body")
        mock_response.text = str(body)

    return mock_response


def _make_session_mock() -> MagicMock:
    """
        Build a :class:`MagicMock` shaped like a :class:`requests.Session`.
        The real ``headers`` attribute is mutable so the transport can
        update it via ``.update(...)``.

        :returns: The configured mock session.
    """
    mock_session = MagicMock()
    mock_session.headers = {}
    mock_session.verify = None
    return mock_session


def _short_aspects() -> UefiCmdAspects:
    """
        Build a :class:`UefiCmdAspects` with very short completion timing
        so TaskMonitor timeout tests do not stall the suite.

        :returns: A fresh UefiCmdAspects instance.
    """
    aspects = UefiCmdAspects(
        completion_timeout=1.0,
        completion_interval=0.1,
        connection_timeout=1.0,
        connection_interval=0.1,
    )
    return aspects


# ---------------------------------------------------------------------------
# TLS verify default
# ---------------------------------------------------------------------------

class TlsVerifyDefaultTestCase(unittest.TestCase):

    def test_verify_true_by_default(self) -> None:
        """
            With ``verify_tls=True`` (credential default) and no CA bundle
            the transport must configure the session with ``verify=True``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )

        captured: Dict[str, Any] = {}

        def _session_factory() -> MagicMock:
            mock_session = _make_session_mock()
            captured["session"] = mock_session
            return mock_session

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   side_effect=_session_factory):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            try:
                self.assertIs(captured["session"].verify, True)
            finally:
                transport.disconnect()

        return

    def test_verify_false_honors_credential(self) -> None:
        """
            A credential with ``verify_tls=False`` must propagate to the
            session as ``verify=False``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
            verify_tls=False,
        )

        captured: Dict[str, Any] = {}

        def _session_factory() -> MagicMock:
            mock_session = _make_session_mock()
            captured["session"] = mock_session
            return mock_session

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   side_effect=_session_factory):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            try:
                self.assertIs(captured["session"].verify, False)
            finally:
                transport.disconnect()

        return

    def test_ca_bundle_path_wins(self) -> None:
        """
            A credential with a ca_bundle path must propagate that path to
            ``session.verify``.
        """
        bundle_path = "/etc/ssl/certs/ca-bundle.pem"
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
            ca_bundle=bundle_path,
        )

        captured: Dict[str, Any] = {}

        def _session_factory() -> MagicMock:
            mock_session = _make_session_mock()
            captured["session"] = mock_session
            return mock_session

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   side_effect=_session_factory):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            try:
                self.assertEqual(captured["session"].verify, bundle_path)
            finally:
                transport.disconnect()

        return


# ---------------------------------------------------------------------------
# Token-based authentication
# ---------------------------------------------------------------------------

class TokenAuthTestCase(unittest.TestCase):

    def test_token_attached_on_get(self) -> None:
        """
            When constructed with a pre-issued token, every request must
            carry the ``X-Auth-Token`` header with that value.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-xyz",
        )

        mock_session = _make_session_mock()
        mock_session.get.return_value = _make_response(
            status_code=200,
            headers={},
            body={"@odata.id": "/redfish/v1/Systems"},
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            result = transport.get(path="/redfish/v1/Systems")
            transport.disconnect()

        self.assertEqual(result, {"@odata.id": "/redfish/v1/Systems"})

        # The GET call should have attached X-Auth-Token in the per-request headers.
        call_args = mock_session.get.call_args
        request_headers = call_args.kwargs["headers"]
        self.assertEqual(request_headers.get("X-Auth-Token"), "tok-xyz")
        self.assertEqual(request_headers.get("OData-Version"), "4.0")
        self.assertEqual(request_headers.get("Accept"), "application/json")

        return

    def test_no_session_login_issued_when_token_preissued(self) -> None:
        """
            When a token is pre-issued the transport must NOT POST to
            ``SessionService/Sessions``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-xyz",
        )

        mock_session = _make_session_mock()

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.disconnect()

        mock_session.post.assert_not_called()
        return


# ---------------------------------------------------------------------------
# Username / password session authentication
# ---------------------------------------------------------------------------

class SessionAuthTestCase(unittest.TestCase):

    def test_session_login_posts_and_captures_token(self) -> None:
        """
            With username + password the transport must POST to the
            Sessions collection, capture ``X-Auth-Token`` from the response
            headers, and attach it on subsequent requests.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )

        mock_session = _make_session_mock()
        mock_session.post.return_value = _make_response(
            status_code=201,
            headers={
                "X-Auth-Token": "tok-from-login",
                "Location": "/redfish/v1/SessionService/Sessions/1",
            },
            body={"@odata.id": "/redfish/v1/SessionService/Sessions/1"},
        )
        mock_session.get.return_value = _make_response(
            status_code=200,
            headers={},
            body={"Systems": {"@odata.id": "/redfish/v1/Systems"}},
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.get(path="/redfish/v1/")

            # Login POST went to the sessions collection with username/password body.
            self.assertEqual(mock_session.post.call_count, 1)
            post_args = mock_session.post.call_args
            self.assertIn(REDFISH_SESSION_COLLECTION_PATH, post_args.args[0])
            self.assertEqual(post_args.kwargs["json"]["UserName"], "admin")
            self.assertEqual(post_args.kwargs["json"]["Password"], "hunter2")

            # Subsequent GET carried the captured token.
            get_args = mock_session.get.call_args
            self.assertEqual(get_args.kwargs["headers"]["X-Auth-Token"], "tok-from-login")

            transport.disconnect()

        return

    def test_failed_session_login_raises_transport_error(self) -> None:
        """
            A non-2xx response to the session login POST must raise
            :class:`UefiTransportError`.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )

        aspects = UefiCmdAspects()

        mock_session = _make_session_mock()
        mock_session.post.return_value = _make_response(
            status_code=403,
            headers={},
            body={"error": "forbidden"},
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential, aspects=aspects)
            with self.assertRaises(UefiTransportError):
                transport.connect()

        return


# ---------------------------------------------------------------------------
# 401 auto-renewal
# ---------------------------------------------------------------------------

class AutoTokenRenewalTestCase(unittest.TestCase):

    def test_401_triggers_renewal_and_retry(self) -> None:
        """
            A ``401`` on a verb must trigger a fresh session login and the
            original request must be retried exactly once with the new
            token.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )

        mock_session = _make_session_mock()

        # First POST: session login (201).  Second POST: session login
        # issued by the 401 auto-renewal path.
        login_response_first = _make_response(
            status_code=201,
            headers={
                "X-Auth-Token": "tok-1",
                "Location": "/redfish/v1/SessionService/Sessions/1",
            },
            body={"@odata.id": "/redfish/v1/SessionService/Sessions/1"},
        )
        login_response_second = _make_response(
            status_code=201,
            headers={
                "X-Auth-Token": "tok-2",
                "Location": "/redfish/v1/SessionService/Sessions/2",
            },
            body={"@odata.id": "/redfish/v1/SessionService/Sessions/2"},
        )
        mock_session.post.side_effect = [login_response_first, login_response_second]

        # GET: first attempt returns 401, second attempt returns 200.
        get_response_unauthorized = _make_response(status_code=401, headers={}, body={})
        get_response_ok = _make_response(
            status_code=200,
            headers={},
            body={"Name": "Systems"},
        )
        mock_session.get.side_effect = [get_response_unauthorized, get_response_ok]

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            result = transport.get(path="/redfish/v1/Systems")
            transport.disconnect()

        self.assertEqual(result, {"Name": "Systems"})
        self.assertEqual(mock_session.get.call_count, 2)
        # The retry GET should have carried the renewed token (tok-2).
        retried_headers = mock_session.get.call_args_list[1].kwargs["headers"]
        self.assertEqual(retried_headers["X-Auth-Token"], "tok-2")

        return

    def test_double_401_raises_transport_error(self) -> None:
        """
            If renewal's login POST itself fails with 401 (or any non-2xx),
            :class:`UefiTransportError` is raised.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )

        mock_session = _make_session_mock()

        login_response_first = _make_response(
            status_code=201,
            headers={"X-Auth-Token": "tok-1", "Location": "/sess/1"},
            body={},
        )
        login_response_second = _make_response(
            status_code=401,
            headers={},
            body={"error": "still unauthorized"},
        )
        mock_session.post.side_effect = [login_response_first, login_response_second]

        get_response_unauthorized = _make_response(status_code=401, headers={}, body={})
        mock_session.get.return_value = get_response_unauthorized

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            with self.assertRaises(UefiTransportError):
                transport.get(path="/redfish/v1/Systems")
            transport.disconnect()

        return


# ---------------------------------------------------------------------------
# 202 TaskMonitor polling
# ---------------------------------------------------------------------------

class TaskMonitorPollingTestCase(unittest.TestCase):

    def test_202_then_running_then_completed(self) -> None:
        """
            A 202 with Location must be polled until the ``TaskState``
            reaches ``Completed``; the final body must be returned.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )

        mock_session = _make_session_mock()

        accepted_response = _make_response(
            status_code=202,
            headers={"Location": "/redfish/v1/TaskService/Tasks/42"},
            body={},
        )
        running_response = _make_response(
            status_code=200,
            headers={},
            body={"TaskState": "Running"},
        )
        completed_response = _make_response(
            status_code=200,
            headers={},
            body={"TaskState": "Completed", "Messages": []},
        )

        # PATCH returns 202, then two GETs poll the task monitor.
        mock_session.patch.return_value = accepted_response
        mock_session.get.side_effect = [running_response, completed_response]

        aspects = _short_aspects()

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            with patch("ccai.protocols.uefi.uefiredfishtransport.time.sleep"):
                transport = UefiRedfishTransport(credential=credential, aspects=aspects)
                transport.connect()
                result = transport.patch(
                    path="/redfish/v1/Systems/1/Bios/Settings",
                    body={"Attributes": {"BootMode": "Uefi"}},
                )
                transport.disconnect()

        self.assertEqual(result["TaskState"], "Completed")
        self.assertEqual(mock_session.get.call_count, 2)
        return

    def test_task_monitor_timeout_raises(self) -> None:
        """
            Continuous ``Running`` state past ``completion_timeout`` must
            raise :class:`TimeoutError`.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )

        mock_session = _make_session_mock()

        accepted_response = _make_response(
            status_code=202,
            headers={"Location": "/redfish/v1/TaskService/Tasks/42"},
            body={},
        )
        running_response = _make_response(
            status_code=200,
            headers={},
            body={"TaskState": "Running"},
        )

        mock_session.post.return_value = accepted_response
        mock_session.get.return_value = running_response

        aspects = UefiCmdAspects(
            completion_timeout=0.5,
            completion_interval=0.1,
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            # Fake monotonic clock: each invocation returns a timestamp 0.25s later,
            # ensuring the deadline elapses after just a handful of polls without
            # sleeping wall-clock time.
            monotonic_steps: List[float] = [0.0]

            def _fake_monotonic() -> float:
                monotonic_steps.append(monotonic_steps[-1] + 0.25)
                rtnval = monotonic_steps[-1]
                return rtnval

            with patch("ccai.protocols.uefi.uefiredfishtransport.time.sleep"):
                with patch("ccai.protocols.uefi.uefiredfishtransport.time.monotonic",
                           side_effect=_fake_monotonic):
                    transport = UefiRedfishTransport(credential=credential, aspects=aspects)
                    transport.connect()
                    with self.assertRaises(TimeoutError):
                        transport.post(path="/redfish/v1/Actions/Foo", body={})
                    transport.disconnect()

        return


# ---------------------------------------------------------------------------
# ETag caching
# ---------------------------------------------------------------------------

class EtagCacheTestCase(unittest.TestCase):

    def test_second_get_sends_if_none_match_and_304_returns_cached(self) -> None:
        """
            GET returns ``ETag``; a second GET includes ``If-None-Match``
            and a 304 response returns the cached body.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )

        mock_session = _make_session_mock()

        first_body = {"Attributes": {"BootMode": "Uefi"}}
        first_response = _make_response(
            status_code=200,
            headers={"ETag": "W/\"v1\""},
            body=first_body,
        )
        not_modified_response = _make_response(
            status_code=304,
            headers={"ETag": "W/\"v1\""},
            body=None,
        )
        mock_session.get.side_effect = [first_response, not_modified_response]

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            first_result = transport.get(path="/redfish/v1/Systems/1/Bios")
            second_result = transport.get(path="/redfish/v1/Systems/1/Bios")
            transport.disconnect()

        # Both calls yield the same body; second came from the cache.
        self.assertEqual(first_result, first_body)
        self.assertEqual(second_result, first_body)

        # The second GET must have carried If-None-Match with the stored ETag.
        second_call_headers = mock_session.get.call_args_list[1].kwargs["headers"]
        self.assertEqual(second_call_headers.get("If-None-Match"), "W/\"v1\"")

        return

    def test_non_get_evicts_cached_etag(self) -> None:
        """
            A non-GET request (e.g. PATCH) must evict the cached ETag for
            the target URL; a subsequent GET must omit ``If-None-Match``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )

        mock_session = _make_session_mock()

        bios_body_v1 = {"Attributes": {"BootMode": "Legacy"}}
        bios_body_v2 = {"Attributes": {"BootMode": "Uefi"}}

        first_get_response = _make_response(
            status_code=200,
            headers={"ETag": "W/\"v1\""},
            body=bios_body_v1,
        )
        patch_response = _make_response(
            status_code=200,
            headers={},
            body={"Accepted": True},
        )
        second_get_response = _make_response(
            status_code=200,
            headers={"ETag": "W/\"v2\""},
            body=bios_body_v2,
        )
        mock_session.get.side_effect = [first_get_response, second_get_response]
        mock_session.patch.return_value = patch_response

        target_path = "/redfish/v1/Systems/1/Bios"

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.get(path=target_path)
            transport.patch(path=target_path, body={"Attributes": {"BootMode": "Uefi"}})
            transport.get(path=target_path)
            transport.disconnect()

        second_get_headers = mock_session.get.call_args_list[1].kwargs["headers"]
        self.assertNotIn("If-None-Match", second_get_headers)
        return


# ---------------------------------------------------------------------------
# Disconnect teardown
# ---------------------------------------------------------------------------

class DisconnectTestCase(unittest.TestCase):

    def test_disconnect_deletes_session_when_we_created_it(self) -> None:
        """
            When the transport created the Redfish session resource,
            :meth:`disconnect` must DELETE it and call ``Session.close()``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )

        mock_session = _make_session_mock()
        mock_session.post.return_value = _make_response(
            status_code=201,
            headers={
                "X-Auth-Token": "tok-from-login",
                "Location": "/redfish/v1/SessionService/Sessions/7",
            },
            body={"@odata.id": "/redfish/v1/SessionService/Sessions/7"},
        )
        mock_session.delete.return_value = _make_response(status_code=204, headers={}, body={})

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.disconnect()

        mock_session.delete.assert_called_once()
        delete_url = mock_session.delete.call_args.args[0]
        self.assertIn("/redfish/v1/SessionService/Sessions/7", delete_url)

        mock_session.close.assert_called_once()
        return

    def test_disconnect_does_not_delete_session_when_token_preissued(self) -> None:
        """
            With a pre-issued token the transport did not create the
            Redfish session and must not DELETE it on disconnect.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-external",
        )

        mock_session = _make_session_mock()

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.disconnect()

        mock_session.delete.assert_not_called()
        mock_session.close.assert_called_once()
        return

    def test_disconnect_is_idempotent(self) -> None:
        """
            Calling :meth:`disconnect` twice must not raise.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-external",
        )

        mock_session = _make_session_mock()

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.disconnect()
            transport.disconnect()

        return


# ---------------------------------------------------------------------------
# Byte-pipe primitives raise SemanticError
# ---------------------------------------------------------------------------

class BytePipeRefusalTestCase(unittest.TestCase):

    def _make_connected_transport(self) -> UefiRedfishTransport:
        """
            Build a connected transport backed by a mock session.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        mock_session = _make_session_mock()
        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
        return transport

    def test_send_raises_semantic_error(self) -> None:
        """
            :meth:`send` must raise :class:`SemanticError`.
        """
        transport = self._make_connected_transport()
        with self.assertRaises(SemanticError):
            transport.send(data=b"AT\r\n")
        return

    def test_recv_raises_semantic_error(self) -> None:
        """
            :meth:`recv` must raise :class:`SemanticError`.
        """
        transport = self._make_connected_transport()
        with self.assertRaises(SemanticError):
            transport.recv(max_bytes=128, timeout=1.0)
        return

    def test_recv_until_raises_semantic_error(self) -> None:
        """
            :meth:`recv_until` must raise :class:`SemanticError`.
        """
        transport = self._make_connected_transport()
        with self.assertRaises(SemanticError):
            transport.recv_until(sentinel=b"\n", timeout=1.0)
        return


# ---------------------------------------------------------------------------
# Connection action patterns
# ---------------------------------------------------------------------------

class ConnectActionPatternTestCase(unittest.TestCase):

    def test_do_while_success_raises_semantic_error(self) -> None:
        """
            :data:`ActionPattern.DO_WHILE_SUCCESS` is not meaningful for
            :meth:`connect` and must raise :class:`SemanticError`.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        aspects = UefiCmdAspects(action_pattern=ActionPattern.DO_WHILE_SUCCESS)
        transport = UefiRedfishTransport(credential=credential, aspects=aspects)
        with self.assertRaises(SemanticError):
            transport.connect()
        return

    def test_single_connected_call_noop_when_already_connected(self) -> None:
        """
            :data:`ActionPattern.SINGLE_CONNECTED_CALL` must be a no-op on
            an already-connected transport.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        mock_session = _make_session_mock()

        session_construct_count: Dict[str, int] = {"count": 0}

        def _factory() -> MagicMock:
            session_construct_count["count"] += 1
            return mock_session

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   side_effect=_factory):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            connected_aspects = UefiCmdAspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
            transport.connect(aspects=connected_aspects)
            transport.disconnect()

        # Only one Session should have been built.
        self.assertEqual(session_construct_count["count"], 1)
        return


# ---------------------------------------------------------------------------
# Properties and structural-typing sanity
# ---------------------------------------------------------------------------

class TransportPropertyTestCase(unittest.TestCase):

    def test_transport_kind_is_redfish(self) -> None:
        """
            :attr:`transport_kind` must report the string ``"redfish"``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        transport = UefiRedfishTransport(credential=credential)
        self.assertEqual(transport.transport_kind, REDFISH_TRANSPORT_KIND)
        return

    def test_connected_false_before_connect(self) -> None:
        """
            :attr:`connected` must be ``False`` before :meth:`connect`.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        transport = UefiRedfishTransport(credential=credential)
        self.assertFalse(transport.connected)
        return


# ---------------------------------------------------------------------------
# Mandatory headers
# ---------------------------------------------------------------------------

class MandatoryHeadersTestCase(unittest.TestCase):

    def test_get_includes_mandatory_headers(self) -> None:
        """
            Every GET must carry ``OData-Version`` and ``Accept``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        mock_session = _make_session_mock()
        mock_session.get.return_value = _make_response(
            status_code=200,
            headers={},
            body={"Name": "Systems"},
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.get(path="/redfish/v1/Systems")
            transport.disconnect()

        headers = mock_session.get.call_args.kwargs["headers"]
        for key, value in MANDATORY_HEADERS.items():
            self.assertEqual(headers.get(key), value)
        return

    def test_post_body_has_content_type(self) -> None:
        """
            POST with a body must carry ``Content-Type: application/json``.
        """
        credential = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="tok-aaa",
        )
        mock_session = _make_session_mock()
        mock_session.post.return_value = _make_response(
            status_code=200,
            headers={},
            body={"Ok": True},
        )

        with patch("ccai.protocols.uefi.uefiredfishtransport.requests.Session",
                   return_value=mock_session):
            transport = UefiRedfishTransport(credential=credential)
            transport.connect()
            transport.post(path="/redfish/v1/Actions/Foo", body={"k": "v"})
            transport.disconnect()

        headers = mock_session.post.call_args.kwargs["headers"]
        self.assertEqual(headers.get("Content-Type"), "application/json")
        return


if __name__ == "__main__":
    unittest.main()
