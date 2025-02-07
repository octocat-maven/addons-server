# -*- coding: utf-8 -*-
from unittest import mock

from django.conf import settings
from django.core import mail

from olympia.abuse.models import AbuseReport, GeoIP2Error, GeoIP2Exception
from olympia.addons.models import Addon
from olympia.amo.tests import addon_factory, TestCase
from olympia.users.models import UserProfile


class TestAbuse(TestCase):
    fixtures = ['base/addon_3615', 'base/user_999']

    def test_choices(self):
        assert AbuseReport.ADDON_SIGNATURES.choices == (
            (None, 'None'),
            (1, 'Curated and partner'),
            (2, 'Curated'),
            (3, 'Partner'),
            (4, 'Non-curated'),
            (5, 'Unsigned'),
            (6, 'Broken'),
            (7, 'Unknown'),
            (8, 'Missing'),
            (9, 'Preliminary'),
            (10, 'Signed'),
            (11, 'System'),
            (12, 'Privileged'),
        )
        assert AbuseReport.ADDON_SIGNATURES.api_choices == (
            (None, None),
            (1, 'curated_and_partner'),
            (2, 'curated'),
            (3, 'partner'),
            (4, 'non_curated'),
            (5, 'unsigned'),
            (6, 'broken'),
            (7, 'unknown'),
            (8, 'missing'),
            (9, 'preliminary'),
            (10, 'signed'),
            (11, 'system'),
            (12, 'privileged'),
        )

        assert AbuseReport.REASONS.choices == (
            (None, 'None'),
            (1, 'Damages computer and/or data'),
            (2, 'Creates spam or advertising'),
            (3, 'Changes search / homepage / new tab page without informing '
                'user'),
            (5, "Doesn’t work, breaks websites, or slows Firefox down"),
            (6, 'Hateful, violent, or illegal content'),
            (7, "Pretends to be something it’s not"),
            (9, "Wasn't wanted / impossible to get rid of"),
            (127, 'Other'),
        )
        assert AbuseReport.REASONS.api_choices == (
            (None, None),
            (1, 'damage'),
            (2, 'spam'),
            (3, 'settings'),
            (5, 'broken'),
            (6, 'policy'),
            (7, 'deceptive'),
            (9, 'unwanted'),
            (127, 'other'),
        )

        assert AbuseReport.ADDON_INSTALL_METHODS.choices == (
            (None, 'None'),
            (1, 'Add-on Manager Web API'),
            (2, 'Direct link'),
            (3, 'Install Trigger'),
            (4, 'From File'),
            (5, 'Webext management API'),
            (6, 'Drag & Drop'),
            (7, 'Sideload'),
            (8, 'File URL'),
            (9, 'Enterprise Policy'),
            (10, 'Included in build'),
            (11, 'System Add-on'),
            (12, 'Temporary Add-on'),
            (13, 'Sync'),
            (127, 'Other')
        )

        assert AbuseReport.ADDON_INSTALL_METHODS.api_choices == (
            (None, None),
            (1, 'amwebapi'),
            (2, 'link'),
            (3, 'installtrigger'),
            (4, 'install_from_file'),
            (5, 'management_webext_api'),
            (6, 'drag_and_drop'),
            (7, 'sideload'),
            (8, 'file_url'),
            (9, 'enterprise_policy'),
            (10, 'distribution'),
            (11, 'system_addon'),
            (12, 'temporary_addon'),
            (13, 'sync'),
            (127, 'other')
        )

        assert AbuseReport.REPORT_ENTRY_POINTS.choices == (
            (None, 'None'),
            (1, 'Uninstall'),
            (2, 'Menu'),
            (3, 'Toolbar context menu'),
        )
        assert AbuseReport.REPORT_ENTRY_POINTS.api_choices == (
            (None, None),
            (1, 'uninstall'),
            (2, 'menu'),
            (3, 'toolbar_context_menu'),
        )

    def test_user(self):
        user = UserProfile.objects.get(pk=999)
        report = AbuseReport.objects.create(user=user)
        report.send()
        assert str(report) == u'[User] Abuse Report for regularuser التطب'
        assert (
            mail.outbox[0].subject ==
            u'[User] Abuse Report for regularuser التطب')
        assert user.get_admin_absolute_url() in mail.outbox[0].body

        assert mail.outbox[0].to == [settings.ABUSE_EMAIL]

    def test_addon(self):
        addon = Addon.objects.get(pk=3615)
        report = AbuseReport.objects.create(addon=addon)
        assert (
            str(report) ==
            u'[Extension] Abuse Report for Delicious Bookmarks')
        report.send()
        assert (
            mail.outbox[0].subject ==
            u'[Extension] Abuse Report for Delicious Bookmarks')
        assert addon.get_admin_absolute_url() in mail.outbox[0].body

    def test_addon_send_metadata(self):
        addon = Addon.objects.get(pk=3615)
        in_the_past = addon.created
        report = AbuseReport.objects.create(
            addon=addon, addon_name='Fôo', addon_summary='Sûmmary',
            install_date=in_the_past, reason=AbuseReport.REASONS.DAMAGE,
            addon_signature=AbuseReport.ADDON_SIGNATURES.CURATED)
        assert report.metadata == {
            'addon_name': 'Fôo',
            'addon_signature': 'Curated',
            'addon_summary': 'Sûmmary',
            'application': 'Firefox',
            'install_date': in_the_past,
            'reason': 'Damages computer and/or data'
        }
        report.send()
        expected_mail_body = (
            """An anonymous user reported abuse for Delicious Bookmarks (%s).

addon_name => Fôo
addon_summary => Sûmmary
addon_signature => Curated
application => Firefox
install_date => 2006-10-23 12:57:41
reason => Damages computer and/or data

""" % addon.get_admin_absolute_url())
        assert mail.outbox[0].body == expected_mail_body

    def test_type_unknown_addon_type(self):
        addon = Addon.objects.get(pk=3615)
        report = AbuseReport.objects.create(addon=addon)
        report.addon.type = -42  # Obviously that type isn't valid.
        assert report.type == 'Addon'  # Doesn't fail.

    def test_addon_fr(self):
        with self.activate(locale='fr'):
            report = AbuseReport(addon_id=3615)
            assert (
                str(report) ==
                u'[Extension] Abuse Report for Delicious Bookmarks')
            report.send()
        assert (
            mail.outbox[0].subject ==
            u'[Extension] Abuse Report for Delicious Bookmarks')

    def test_guid(self):
        report = AbuseReport.objects.create(guid='foo@bar.org')
        report.send()
        assert str(report) == u'[Addon] Abuse Report for foo@bar.org'
        assert (
            mail.outbox[0].subject ==
            u'[Addon] Abuse Report for foo@bar.org')
        assert (
            'An anonymous user reported abuse for foo@bar.org '
            in mail.outbox[0].body)
        assert report.get_admin_absolute_url() in mail.outbox[0].body

    @mock.patch('olympia.abuse.models.GeoIP2')
    def test_lookup_country_code_from_ip(self, GeoIP2_mock):
        GeoIP2_mock.return_value.country_code.return_value = 'ZZ'
        assert AbuseReport.lookup_country_code_from_ip('') == ''
        assert AbuseReport.lookup_country_code_from_ip('notanip') == ''
        assert GeoIP2_mock.return_value.country_code.call_count == 0

        GeoIP2_mock.return_value.country_code.return_value = 'ZZ'
        assert AbuseReport.lookup_country_code_from_ip('127.0.0.1') == 'ZZ'
        assert AbuseReport.lookup_country_code_from_ip('::1') == 'ZZ'

        GeoIP2_mock.return_value.country_code.side_effect = GeoIP2Exception
        assert AbuseReport.lookup_country_code_from_ip('127.0.0.1') == ''

        GeoIP2_mock.return_value.country_code.side_effect = GeoIP2Error
        assert AbuseReport.lookup_country_code_from_ip('127.0.0.1') == ''

    def test_save_soft_deleted(self):
        report = AbuseReport.objects.create()
        report.delete()
        report.reason = AbuseReport.REASONS.SPAM
        report.save()
        assert report.reason == AbuseReport.REASONS.SPAM


class TestAbuseManager(TestCase):
    def test_deleted(self):
        report = AbuseReport.objects.create()
        deleted_report = AbuseReport.objects.create()
        assert AbuseReport.objects.count() == 2
        assert AbuseReport.unfiltered.count() == 2

        deleted_report.delete()

        assert deleted_report.state == AbuseReport.STATES.DELETED
        assert deleted_report.pk
        assert report in AbuseReport.objects.all()
        assert deleted_report not in AbuseReport.objects.all()
        assert AbuseReport.objects.count() == 1

        assert report in AbuseReport.unfiltered.all()
        assert deleted_report in AbuseReport.unfiltered.all()
        assert AbuseReport.unfiltered.count() == 2

    def test_deleted_related(self):
        addon = addon_factory()
        report = AbuseReport.objects.create(addon=addon)
        deleted_report = AbuseReport.objects.create(addon=addon)
        assert addon.abuse_reports.count() == 2

        deleted_report.delete()

        assert report in addon.abuse_reports.all()
        assert deleted_report not in addon.abuse_reports.all()
        assert addon.abuse_reports.count() == 1
