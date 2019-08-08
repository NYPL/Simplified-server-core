import os
import json

import flask
from flask import Flask
from flask_babel import (
    Babel,
    lazy_gettext as _
)
from nose.tools import (
    assert_raises,
    assert_raises_regexp,
    eq_,
    set_trace,
)

from . import (
    DatabaseTest,
)

from ..opds import TestAnnotator

from ..model import (
    Identifier,
    ConfigurationSetting,
)

from ..lane import (
    Facets,
    Pagination,
    SearchFacets,
    WorkList,
)

from ..app_server import (
    HeartbeatController,
    URNLookupController,
    ErrorHandler,
    ComplaintController,
    load_facets_from_request,
    load_pagination_from_request,
)

from ..config import Configuration

from ..log import LogConfiguration

from ..entrypoint import (
    AudiobooksEntryPoint,
    EbooksEntryPoint,
    EntryPoint,
)

from ..problem_details import (
    INVALID_INPUT,
    INVALID_URN,
)

from ..util.opds_writer import (
    OPDSFeed,
    OPDSMessage,
)


class TestHeartbeatController(object):

    def test_heartbeat(self):
        app = Flask(__name__)
        controller = HeartbeatController()

        with app.test_request_context('/'):
            response = controller.heartbeat()
        eq_(200, response.status_code)
        eq_(controller.HEALTH_CHECK_TYPE, response.headers.get('Content-Type'))
        data = json.loads(response.data.decode("utf8"))
        eq_('pass', data['status'])

        # Create a .version file.
        root_dir = os.path.join(os.path.split(__file__)[0], "..", "..")
        version_filename = os.path.join(root_dir, controller.VERSION_FILENAME)
        with open(version_filename, 'w') as f:
            f.write('ba.na.na-10-ssssssssss')

        # Create a mock configuration object to test with.
        class MockConfiguration(Configuration):
            instance = dict()

        with app.test_request_context('/'):
            response = controller.heartbeat(conf_class=MockConfiguration)
        if os.path.exists(version_filename):
            os.remove(version_filename)

        eq_(200, response.status_code)
        content_type = response.headers.get('Content-Type')
        eq_(controller.HEALTH_CHECK_TYPE, content_type)

        data = json.loads(response.data.decode("utf8"))
        eq_('pass', data['status'])
        eq_('ba.na.na', data['version'])
        eq_('ba.na.na-10-ssssssssss', data['releaseID'])


class TestURNLookupController(DatabaseTest):

    def setup(self):
        super(TestURNLookupController, self).setup()
        self.controller = URNLookupController(self._db)

    def assert_one_message(self, urn, code, message):
        """Assert that the given message is the only thing
        in the feed.
        """
        [obj] = self.controller.precomposed_entries
        expect = OPDSMessage(urn, code, message)
        assert isinstance(obj, OPDSMessage)
        eq_(urn, obj.urn)
        eq_(code, obj.status_code)
        eq_(message, obj.message)
        eq_([], self.controller.works)

    def test_process_urns_invalid_urn(self):
        urn = "not even a URN"
        self.controller.process_urns([urn])
        self.assert_one_message(urn, 400, INVALID_URN.detail)

    def test_process_urns_unrecognized_identifier(self):
        # Give the controller a URN that, although valid, doesn't
        # correspond to any Identifier in the database.
        urn = Identifier.GUTENBERG_URN_SCHEME_PREFIX + 'Gutenberg%20ID/000'
        self.controller.process_urns([urn])

        # The result is a 404 message.
        self.assert_one_message(
            urn, 404, self.controller.UNRECOGNIZED_IDENTIFIER
        )

    def test_process_identifier_no_license_pool(self):
        # Give the controller a URN that corresponds to an Identifier
        # which has no LicensePool.
        identifier = self._identifier()
        self.controller.process_identifier(identifier, identifier.urn)

        # The result is a 404 message.
        self.assert_one_message(
            identifier.urn, 404, self.controller.UNRECOGNIZED_IDENTIFIER
        )

    def test_process_identifier_license_pool_but_no_work(self):
        edition, pool = self._edition(with_license_pool=True)
        identifier = edition.primary_identifier
        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, 202, self.controller.WORK_NOT_CREATED
        )

    def test_process_identifier_work_not_presentation_ready(self):
        work = self._work(with_license_pool=True)
        work.presentation_ready = False
        identifier = work.license_pools[0].identifier
        self.controller.process_identifier(identifier, identifier.urn)

        self.assert_one_message(
            identifier.urn, 202, self.controller.WORK_NOT_PRESENTATION_READY
        )

    def test_process_identifier_work_is_presentation_ready(self):
        work = self._work(with_license_pool=True)
        identifier = work.license_pools[0].identifier
        self.controller.process_identifier(identifier, identifier.urn)
        eq_([], self.controller.precomposed_entries)
        eq_([(work.presentation_edition.primary_identifier, work)],
            self.controller.works
        )

    # Set up a mock Flask app for testing the controller methods.
    app = Flask(__name__)
    @app.route('/lookup')
    def lookup(self, urn):
        pass
    @app.route('/work')
    def work(self, urn):
        pass

    def test_work_lookup(self):
        work = self._work(with_license_pool=True)
        identifier = work.license_pools[0].identifier
        annotator = TestAnnotator()
        with self.app.test_request_context("/?urn=%s" % identifier.urn):
            response = self.controller.work_lookup(
                annotator=annotator
            )

            # We got an OPDS feed that includes an entry for the work.
            eq_(200, response.status_code)
            eq_(OPDSFeed.ACQUISITION_FEED_TYPE,
                response.headers['Content-Type'])
            response_data = response.data.decode("utf8")
            assert identifier.urn in response_data
            assert work.title in response_data

    def test_permalink(self):
        work = self._work(with_license_pool=True)
        work.license_pools[0].open_access = False
        identifier = work.license_pools[0].identifier
        annotator = TestAnnotator()
        with self.app.test_request_context("/?urn=%s" % identifier.urn):
            response = self.controller.permalink(identifier.urn, annotator)

            # We got an OPDS feed that includes an entry for the work.
            eq_(200, response.status_code)
            eq_(OPDSFeed.ACQUISITION_FEED_TYPE,
                response.headers['Content-Type'])
            response_data = response.data.decode("utf8")
            assert identifier.urn in response_data
            assert work.title in response_data


class TestComplaintController(DatabaseTest):

    def setup(self):
        super(TestComplaintController, self).setup()
        self.controller = ComplaintController()
        self.edition, self.pool = self._edition(with_license_pool=True)
        self.app = Flask(__name__)
        Babel(self.app)

    def test_no_license_pool(self):
        with self.app.test_request_context("/"):
            response = self.controller.register(None, "{}")
        assert response.status.startswith('400')
        body = json.loads(response.data.decode("utf8"))
        eq_("No license pool specified", body['title'])

    def test_invalid_document(self):
        with self.app.test_request_context("/"):
            response = self.controller.register(self.pool, "not {a} valid document")
        assert response.status.startswith('400')
        body = json.loads(response.data.decode("utf8"))
        eq_("Invalid problem detail document", body['title'])

    def test_invalid_type(self):
        data = json.dumps({"type": "http://not-a-recognized-type/"})
        with self.app.test_request_context("/"):
            response = self.controller.register(self.pool, data)
        assert response.status.startswith('400')
        body = json.loads(response.data.decode("utf8"))
        eq_("Unrecognized problem type: http://not-a-recognized-type/",
            body['title']
        )

    def test_success(self):
        data = json.dumps(
            {
                "type": "http://librarysimplified.org/terms/problem/wrong-genre",
                "source": "foo",
                "detail": "bar",
            }
        )
        with self.app.test_request_context("/"):
            response = self.controller.register(self.pool, data)
        assert response.status.startswith('201')
        [complaint] = self.pool.complaints
        eq_("foo", complaint.source)
        eq_("bar", complaint.detail)


class TestLoadMethods(DatabaseTest):

    def setup(self):
        super(TestLoadMethods, self).setup()
        self.app = Flask(__name__)
        Babel(self.app)

    def test_load_facets_from_request(self):
        # The library has two EntryPoints enabled.
        self._default_library.setting(EntryPoint.ENABLED_SETTING).value = (
            json.dumps([EbooksEntryPoint.INTERNAL_NAME,
                        AudiobooksEntryPoint.INTERNAL_NAME])
        )

        with self.app.test_request_context('/?order=%s' % Facets.ORDER_TITLE):
            flask.request.library = self._default_library
            facets = load_facets_from_request()
            eq_(Facets.ORDER_TITLE, facets.order)
            # Enabled facets are passed in to the newly created Facets,
            # in case the load method received a custom config.
            assert facets.facets_enabled_at_init != None

        with self.app.test_request_context('/?order=bad_facet'):
            flask.request.library = self._default_library
            problemdetail = load_facets_from_request()
            eq_(INVALID_INPUT.uri, problemdetail.uri)

        # An EntryPoint will be picked up from the request and passed
        # into the Facets object, assuming the EntryPoint is
        # configured on the present library.
        worklist = WorkList()
        worklist.initialize(self._default_library)
        with self.app.test_request_context('/?entrypoint=Audio'):
            flask.request.library = self._default_library
            facets = load_facets_from_request(worklist=worklist)
            eq_(AudiobooksEntryPoint, facets.entrypoint)
            eq_(False, facets.entrypoint_is_default)

        # If the requested EntryPoint not configured, the default
        # EntryPoint is used.
        with self.app.test_request_context('/?entrypoint=NoSuchEntryPoint'):
            flask.request.library = self._default_library
            default_entrypoint=object()
            facets = load_facets_from_request(
                worklist=worklist, default_entrypoint=default_entrypoint
            )
            eq_(default_entrypoint, facets.entrypoint)
            eq_(True, facets.entrypoint_is_default)

        # Load a SearchFacets object that pulls information from an
        # HTTP header.
        with self.app.test_request_context(
                '/', headers = {'Accept-Language' : 'ja' }
        ):
            flask.request.library = self._default_library
            facets = load_facets_from_request(base_class=SearchFacets)
            eq_(['jpn'], facets.languages)

    def test_load_facets_from_request_class_instantiation(self):
        """The caller of load_facets_from_request() can specify a class other
        than Facets to call from_request() on.
        """
        class MockFacets(object):
            @classmethod
            def from_request(*args, **kwargs):
                facets = MockFacets()
                facets.called_with = kwargs
                return facets
        kwargs = dict(some_arg='some value')
        with self.app.test_request_context(''):
            flask.request.library = self._default_library
            facets = load_facets_from_request(
                None, None, base_class=MockFacets,
                base_class_constructor_kwargs=kwargs
            )
        assert isinstance(facets, MockFacets)
        eq_('some value', facets.called_with['some_arg'])

    def test_load_pagination_from_request(self):
        # Verify that load_pagination_from_request insantiates a
        # pagination object of the specified class (Pagination, by
        # default.)
        class Mock(object):
            DEFAULT_SIZE = 22

            @classmethod
            def from_request(cls, get_arg, default_size, **kwargs):
                cls.called_with = (get_arg, default_size, kwargs)
                return "I'm a pagination object!"

        with self.app.test_request_context('/'):
            # Call load_pagination_from_request and verify that
            # Mock.from_request was called with the arguments we expect.
            extra_kwargs = dict(extra='kwarg')
            pagination = load_pagination_from_request(
                base_class=Mock, base_class_constructor_kwargs=extra_kwargs,
                default_size=44
            )
            eq_("I'm a pagination object!", pagination)
            eq_((flask.request.args.get, 44, extra_kwargs),
                Mock.called_with)

        # If no default size is specified, we trust from_request to
        # use the class default.
        with self.app.test_request_context('/'):
            pagination = load_pagination_from_request(base_class=Mock)
            eq_((flask.request.args.get, None, {}),
                Mock.called_with)

        # Now try a real case using the default pagination class,
        # Pagination
        with self.app.test_request_context('/?size=50&after=10'):
            pagination = load_pagination_from_request()
            assert isinstance(pagination, Pagination)
            eq_(50, pagination.size)
            eq_(10, pagination.offset)

        # Tests of from_request() are found in the tests of the various
        # pagination classes.


class CanBeProblemDetailDocument(Exception):
    """A fake exception that can be represented as a problem
    detail document.
    """

    def as_problem_detail_document(self, debug):
        return INVALID_URN.detailed(
            _("detail info"),
            debug_message="A debug_message which should only appear in debug mode."
        )


class TestErrorHandler(DatabaseTest):

    def setup(self):
        super(TestErrorHandler, self).setup()

        class MockManager(object):
            """Simulate an application manager object such as
            the circulation manager's CirculationManager.

            This gives ErrorHandler access to a database connection.
            """
            _db = self._db

        self.app = Flask(__name__)
        self.app.manager = MockManager()
        Babel(self.app)

    def activate_debug_mode(self):
        """Set a site-wide setting that controls whether
        detailed exception information is provided.
        """
        ConfigurationSetting.sitewide(
            self._db, Configuration.DATABASE_LOG_LEVEL
        ).value = LogConfiguration.DEBUG

    def raise_exception(self, cls=Exception):
        """Simulate an exception that happens deep within the stack."""
        raise cls()

    def test_unhandled_error(self):
        handler = ErrorHandler(self.app)
        with self.app.test_request_context('/'):
            response = None
            try:
                self.raise_exception()
            except Exception as exception:
                response = handler.handle(exception)
            eq_(500, response.status_code)
            eq_("An internal error occured", response.data.decode("utf8"))


    def test_unhandled_error_debug(self):
        # Set the sitewide log level to DEBUG to get a stack trace
        # instead of a generic error message.
        handler = ErrorHandler(self.app)
        self.activate_debug_mode()

        with self.app.test_request_context('/'):
            response = None
            try:
                self.raise_exception()
            except Exception as exception:
                response = handler.handle(exception)
            eq_(500, response.status_code)
            assert response.data.startswith(b'Traceback (most recent call last)')


    def test_handle_error_as_problem_detail_document(self):
        handler = ErrorHandler(self.app)
        with self.app.test_request_context('/'):
            try:
                self.raise_exception(CanBeProblemDetailDocument)
            except Exception as exception:
                response = handler.handle(exception)

            eq_(400, response.status_code)
            data = json.loads(response.data.decode("utf8"))
            eq_(INVALID_URN.title, data['title'])

            # Since we are not in debug mode, the debug_message is
            # destroyed.
            assert 'debug_message' not in data

    def test_handle_error_as_problem_detail_document_debug(self):
        # When in debug mode, the debug_message is preserved and a
        # stack trace is appended to it.
        handler = ErrorHandler(self.app)
        self.activate_debug_mode()
        with self.app.test_request_context('/'):
            try:
                self.raise_exception(CanBeProblemDetailDocument)
            except Exception as exception:
                response = handler.handle(exception)

            eq_(400, response.status_code)
            data = json.loads(response.data.decode("utf8"))
            eq_(INVALID_URN.title, data['title'])
            assert data['debug_message'].startswith(
                "A debug_message which should only appear in debug mode.\n\n"
                'Traceback (most recent call last)'
            )
