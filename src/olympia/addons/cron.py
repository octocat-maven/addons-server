import os
import time

from datetime import datetime, timedelta

from django.conf import settings
from django.db import connections
from django.db.models import Avg, F, Q, Sum

import multidb
import waffle

from celery import group

import olympia.core.logger

from olympia import amo
from olympia.addons.models import Addon, FrozenAddon
from olympia.addons.tasks import (
    update_addon_average_daily_users as _update_addon_average_daily_users,
    update_addon_download_totals as _update_addon_download_totals,
    update_appsupport)
from olympia.amo.decorators import use_primary_db
from olympia.amo.utils import chunked
from olympia.files.models import File
from olympia.lib.es.utils import raise_if_reindex_in_progress
from olympia.stats.models import UpdateCount


log = olympia.core.logger.getLogger('z.cron')
task_log = olympia.core.logger.getLogger('z.task')


def update_addon_average_daily_users():
    """Update add-ons ADU totals."""
    if not waffle.switch_is_active('local-statistics-processing'):
        return False

    raise_if_reindex_in_progress('amo')
    cursor = connections[multidb.get_replica()].cursor()
    q = """SELECT addon_id, AVG(`count`)
           FROM update_counts
           WHERE `date` > DATE_SUB(CURDATE(), INTERVAL 13 DAY)
           GROUP BY addon_id
           ORDER BY addon_id"""
    cursor.execute(q)
    d = cursor.fetchall()
    cursor.close()

    ts = [_update_addon_average_daily_users.subtask(args=[chunk])
          for chunk in chunked(d, 250)]
    group(ts).apply_async()


def update_addon_download_totals():
    """Update add-on total and average downloads."""
    if not waffle.switch_is_active('local-statistics-processing'):
        return False

    qs = (
        Addon.objects
             .annotate(sum_download_count=Sum('downloadcount__count'))
             .values_list('id', 'sum_download_count')
             .order_by('id')
    )
    ts = [_update_addon_download_totals.subtask(args=[chunk])
          for chunk in chunked(qs, 250)]
    group(ts).apply_async()


def _change_last_updated(next):
    # We jump through some hoops here to make sure we only change the add-ons
    # that really need it, and to invalidate properly.
    current = dict(Addon.objects.values_list('id', 'last_updated'))
    changes = {}

    for addon, last_updated in next.items():
        try:
            if current[addon] != last_updated:
                changes[addon] = last_updated
        except KeyError:
            pass

    if not changes:
        return

    log.debug('Updating %s add-ons' % len(changes))
    # Update + invalidate.
    qs = Addon.objects.filter(id__in=changes).no_transforms()
    for addon in qs:
        addon.update(last_updated=changes[addon.id])


@use_primary_db
def addon_last_updated():
    next = {}
    for q in Addon._last_updated_queries().values():
        for addon, last_updated in q.values_list('id', 'last_updated'):
            next[addon] = last_updated

    _change_last_updated(next)

    # Get anything that didn't match above.
    other = (Addon.objects.filter(last_updated__isnull=True)
             .values_list('id', 'created'))
    _change_last_updated(dict(other))


def update_addon_appsupport():
    # Find all the add-ons that need their app support details updated.
    newish = (Q(last_updated__gte=F('appsupport__created')) |
              Q(appsupport__created__isnull=True))
    # Search providers don't list supported apps.
    has_app = Q(versions__apps__isnull=False) | Q(type=amo.ADDON_SEARCH)
    has_file = Q(versions__files__status__in=amo.VALID_FILE_STATUSES)
    good = Q(has_app, has_file)
    ids = (Addon.objects.valid().distinct()
           .filter(newish, good).values_list('id', flat=True))

    task_log.info('Updating appsupport for %d new-ish addons.' % len(ids))
    ts = [update_appsupport.subtask(args=[chunk])
          for chunk in chunked(ids, 20)]
    group(ts).apply_async()


def hide_disabled_files():
    """
    Move files (on filesystem) belonging to disabled files (in database) to the
    correct place if necessary, so they they are not publicly accessible
    any more.

    See also unhide_disabled_files().
    """
    ids = (File.objects.filter(
        Q(version__addon__status=amo.STATUS_DISABLED) |
        Q(version__addon__disabled_by_user=True) |
        Q(status=amo.STATUS_DISABLED)).values_list('id', flat=True))
    for chunk in chunked(ids, 300):
        qs = File.objects.select_related('version').filter(id__in=chunk)
        for file_ in qs:
            # This tries to move the file to the disabled location. If it
            # didn't exist at the source, it will catch the exception, log it
            # and continue.
            file_.hide_disabled_file()


def unhide_disabled_files():
    """
    Move files (on filesystem) belonging to public files (in database) to the
    correct place if necessary, so they they publicly accessible.

    See also hide_disabled_files().
    """
    ids = (File.objects.exclude(
        Q(version__addon__status=amo.STATUS_DISABLED) |
        Q(version__addon__disabled_by_user=True) |
        Q(status=amo.STATUS_DISABLED)).values_list('id', flat=True))
    for chunk in chunked(ids, 300):
        qs = File.objects.select_related('version').filter(id__in=chunk)
        for file_ in qs:
            # This tries to move the file to the public location. If it
            # didn't exist at the source, it will catch the exception, log it
            # and continue.
            file_.unhide_disabled_file()


def deliver_hotness():
    """
    Calculate hotness of all add-ons.

    a = avg(users this week)
    b = avg(users three weeks before this week)
    hotness = (a-b) / b if a > 1000 and b > 1 else 0
    """
    frozen = set(f.id for f in FrozenAddon.objects.all())
    all_ids = list((Addon.objects.filter(status__in=amo.REVIEWED_STATUSES)
                   .values_list('id', flat=True)))
    now = datetime.now()
    one_week = now - timedelta(days=7)
    four_weeks = now - timedelta(days=28)

    for ids in chunked(all_ids, 300):
        addons = Addon.objects.filter(id__in=ids).no_transforms()
        ids = [a.id for a in addons if a.id not in frozen]
        qs = (UpdateCount.objects.filter(addon__in=ids)
              .values_list('addon').annotate(Avg('count')))
        thisweek = dict(qs.filter(date__gte=one_week))
        threeweek = dict(qs.filter(date__range=(four_weeks, one_week)))
        for addon in addons:
            this, three = thisweek.get(addon.id, 0), threeweek.get(addon.id, 0)

            # Update the hotness score but only update hotness if necessary.
            # We don't want to cause unnecessary re-indexes
            if this > 1000 and three > 1:
                hotness = (this - three) / float(three)
                if addon.hotness != hotness:
                    addon.update(hotness=(this - three) / float(three))
            else:
                if addon.hotness != 0:
                    addon.update(hotness=0)

        # Let the database catch its breath.
        time.sleep(10)


def cleanup_image_files():
    """
    Clean up all header images files for themes.

    We use these images to asynchronuously generate thumbnails with
    tasks, here we delete images that are older than one day.

    """
    log.info('Removing one day old temporary image files for themes.')
    for folder in ('persona_header', ):
        root = os.path.join(settings.TMP_PATH, folder)
        if not os.path.exists(root):
            continue
        for path in os.listdir(root):
            full_path = os.path.join(root, path)
            age = time.time() - os.stat(full_path).st_atime
            if age > 60 * 60 * 24:  # One day.
                log.debug('Removing image file: %s, %dsecs old.' %
                          (full_path, age))
                os.unlink(full_path)
