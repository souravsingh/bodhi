# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import math

from datetime import datetime, timedelta
from cornice import Service
from pyramid.exceptions import HTTPNotFound
from sqlalchemy.sql import or_

from bodhi import log
from bodhi.models import Update, Build, Package, Release
import bodhi.schemas
import bodhi.security
from bodhi.validators import (
    validate_nvrs,
    validate_version,
    validate_uniqueness,
    validate_tags,
    validate_acls,
    validate_builds,
    validate_enums,
    validate_updates,
    validate_packages,
    validate_releases,
    validate_release,
    validate_username,
    validate_groups,
)


release = Service(name='release', path='/releases/{name}',
                  description='Fedora Releases')
releases = Service(name='releases', path='/releases/',
                   description='Fedora Releases')

@release.get(accept="text/html", renderer="release.html")
def get_release_html(request):
    id = request.matchdict.get('name')
    release = Release.get(id, request.db)
    if not release:
        request.errors.add('body', 'name', 'No such release')
        request.errors.status = HTTPNotFound.code
    updates = request.db.query(Update).filter(
        Update.release==release).order_by(
            Update.date_submitted.desc())

    updates_count = request.db.query(Update.date_submitted, Update.type).filter(
        Update.release==release).order_by(
            Update.date_submitted.desc())

    now = updates[0].date_submitted
    year = timedelta(days=365)
    diff = now - year

    date_commits = {}
    dates = set()

    for update in updates_count.all():
        d = update.date_submitted
        yearmonth = str(d.year) + '/' + str(d.month).zfill(2)
        dates.add(yearmonth)
        if not update.type.description in date_commits:
            date_commits[update.type.description] = {}
        if yearmonth in date_commits[update.type.description]:
            date_commits[update.type.description][yearmonth] += 1
        else:
            date_commits[update.type.description][yearmonth] = 0

    return dict(release=release,
                latest_updates=updates.limit(25).all(),
                count=updates.count(),
                date_commits=date_commits,
                dates = sorted(dates))

@release.get(accept=('application/json', 'text/json'), renderer='json')
@release.get(accept=('application/javascript'), renderer='jsonp')
def get_release_json(request):
    id = request.matchdict.get('name')
    release = Release.get(id, request.db)
    if not release:
        request.errors.add('body', 'name', 'No such release')
        request.errors.status = HTTPNotFound.code
    return release

@releases.get(accept="text/html", schema=bodhi.schemas.ListReleaseSchema,
              renderer='releases.html',
              validators=(validate_release, validate_updates,
                          validate_packages))
def query_releases_html(request):
    def collect_releases(releases):
        x = {}
        for r in releases:
            if r['state'] in x:
                x[r['state']].append(r)
            else:
                x[r['state']] = [r]
        return x

    db = request.db
    releases = db.query(Release).order_by(Release.id.desc()).all()
    return dict(releases=collect_releases(releases))

@releases.get(accept=('application/json', 'text/json'),
              schema=bodhi.schemas.ListReleaseSchema, renderer='json',
              validators=(validate_release, validate_updates,
                          validate_packages))
def query_releases_json(request):
    db = request.db
    data = request.validated
    query = db.query(Release)

    name = data.get('name')
    if name is not None:
        query = query.filter(Release.name.like(name))

    updates = data.get('updates')
    if updates is not None:
        query = query.join(Release.builds).join(Build.update)
        args = \
            [Update.title == update.title for update in updates] +\
            [Update.alias == update.alias for update in updates]
        query = query.filter(or_(*args))

    packages = data.get('packages')
    if packages is not None:
        query = query.join(Release.builds).join(Build.package)
        query = query.filter(or_(*[Package.id == p.id for p in packages]))

    total = query.count()

    page = data.get('page')
    rows_per_page = data.get('rows_per_page')
    pages = int(math.ceil(total / float(rows_per_page)))
    query = query.offset(rows_per_page * (page - 1)).limit(rows_per_page)

    return dict(
        releases=query.all(),
        page=page,
        pages=pages,
        rows_per_page=rows_per_page,
        total=total,
    )

@releases.post(schema=bodhi.schemas.SaveReleaseSchema,
               acl=bodhi.security.admin_only_acl, renderer='json',
               validators=(validate_tags, validate_enums)
               )
def save_release(request):
    """Save a release

    This entails either creating a new release, or editing an existing one. To
    edit an existing release, the release's original name must be specified in
    the ``edited`` parameter.
    """
    data = request.validated

    edited = data.pop("edited", None)

    try:
        if edited is None:
            log.info("Creating a new release: %s" % data['name'])
            r = Release(**data)

        else:
            log.info("Editing release: %s" % edited)
            r = request.db.query(Release).filter(Release.name==edited).one()
            for k, v in data.items():
                setattr(r, k, v)

    except Exception as e:
        log.exception(e)
        request.errors.add('body', 'release',
                           'Unable to create update: %s' % e)
        return


    request.db.add(r)
    request.db.flush()

    return r
