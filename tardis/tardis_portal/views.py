# -*- coding: utf-8 -*-
#
# Copyright (c) 2010-2011, Monash e-Research Centre
#   (Monash University, Australia)
# Copyright (c) 2010-2011, VeRSI Consortium
#   (Victorian eResearch Strategic Initiative, Australia)
# All rights reserved.
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    *  Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    *  Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#    *  Neither the name of the VeRSI, the VeRSI Consortium members, nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE REGENTS AND CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
"""
views.py

.. moduleauthor:: Steve Androulakis <steve.androulakis@monash.edu>
.. moduleauthor:: Gerson Galang <gerson.galang@versi.edu.au>
.. moduleauthor:: Ulrich Felzmaann <ulrich.felzmann@versi.edu.au>

"""

from base64 import b64decode
import urllib2
from urllib import urlencode, urlopen
from os import path
import logging
import json
from operator import itemgetter

from django.template import Context
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render_to_response
from django.contrib.auth.models import User, Group, AnonymousUser
from django.http import HttpResponseRedirect, HttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required, permission_required
from django.core.urlresolvers import reverse
from django.core.paginator import Paginator, InvalidPage, EmptyPage
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from tardis.urls import getTardisApps
from tardis.tardis_portal.ProcessExperiment import ProcessExperiment
from tardis.tardis_portal.forms import ExperimentForm, \
    createSearchDatafileForm, createSearchDatafileSelectionForm, \
    LoginForm, RegisterExperimentForm, createSearchExperimentForm, \
    ChangeGroupPermissionsForm, ChangeUserPermissionsForm, \
    ImportParamsForm, create_parameterset_edit_form, \
    save_datafile_edit_form, create_datafile_add_form,\
    save_datafile_add_form, MXDatafileSearchForm

from tardis.tardis_portal.errors import UnsupportedSearchQueryTypeError
from tardis.tardis_portal.staging import add_datafile_to_dataset,\
    staging_traverse, write_uploaded_file_to_dataset,\
    get_full_staging_path
from tardis.tardis_portal.models import Experiment, ExperimentParameter, \
    DatafileParameter, DatasetParameter, ExperimentACL, Dataset_File, \
    DatafileParameterSet, ParameterName, GroupAdmin, Schema, \
    Dataset, ExperimentParameterSet, DatasetParameterSet, \
    UserProfile, UserAuthentication, Token

from tardis.tardis_portal import constants
from tardis.tardis_portal.auth.localdb_auth import django_user, django_group
from tardis.tardis_portal.auth.localdb_auth import auth_key as localdb_auth_key
from tardis.tardis_portal.auth import decorators as authz
from tardis.tardis_portal.auth import auth_service
from tardis.tardis_portal.shortcuts import render_response_index, \
    return_response_error, return_response_not_found, \
    render_response_search
from tardis.tardis_portal.metsparser import parseMets
from tardis.tardis_portal.creativecommonshandler import CreativeCommonsHandler
from tardis.tardis_portal.hacks import oracle_dbops_hack

from haystack.views import SearchView
from haystack.query import SearchQuerySet
from tardis.tardis_portal.search_query import FacetFixedSearchQuery
from tardis.tardis_portal.forms import RawSearchForm
from tardis.tardis_portal.search_backend import HighlightSearchBackend
from django.contrib.auth import logout as django_logout

logger = logging.getLogger(__name__)



def getNewSearchDatafileSelectionForm(initial=None):
    DatafileSelectionForm = createSearchDatafileSelectionForm(initial)
    return DatafileSelectionForm()


def logout(request):
    if 'datafileResults' in request.session:
        del request.session['datafileResults']

    c = Context({})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/index.html', c))


def index(request):
    status = ''

    c = Context({'status': status})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/index.html', c))


def site_settings(request):

    if request.method == 'POST':
        if 'username' in request.POST and 'password' in request.POST:

            user = auth_service.authenticate(request=request,
                                             authMethod=localdb_auth_key)
            if user is not None:
                if user.is_staff:

                    x509 = open(settings.GRID_PROXY_FILE, 'r')

                    c = Context({'baseurl': request.build_absolute_uri('/'),
                        'proxy': x509.read(), 'filestorepath':
                        settings.FILE_STORE_PATH})
                    return HttpResponse(render_response_index(request,
                            'tardis_portal/site_settings.xml', c),
                            mimetype='application/xml')

    return return_response_error(request)


@never_cache
def load_image(request, experiment_id, parameter):
    file_path = path.abspath(path.join(settings.FILE_STORE_PATH,
                                       str(experiment_id),
                                       parameter.string_value))

    from django.core.servers.basehttp import FileWrapper
    wrapper = FileWrapper(file(file_path))
    return HttpResponse(wrapper, mimetype=parameter.name.units)



def load_experiment_image(request, parameter_id):
    parameter = ExperimentParameter.objects.get(pk=parameter_id)
    experiment_id = parameter.parameterset.experiment.id
    if authz.has_experiment_access(request, experiment_id):
        return load_image(request, experiment_id, parameter)
    else:
        return return_response_error(request)


def load_dataset_image(request, parameter_id):
    parameter = DatafileParameter.objects.get(pk=parameter_id)
    dataset = parameter.parameterset.dataset
    experiment_id = dataset.experiment.id
    if  authz.has_dataset_access(request, dataset.id):
        return load_image(request, experiment_id, parameter)
    else:
        return return_response_error(request)


def load_datafile_image(request, parameter_id):
    parameter = DatafileParameter.objects.get(pk=parameter_id)
    dataset_file = parameter.parameterset.dataset_file
    experiment_id = dataset_file.dataset.experiment.id
    if authz.has_datafile_access(request, dataset_file.id):
        return load_image(request, experiment_id, parameter)
    else:
        return return_response_error(request)


@authz.experiment_access_required
def display_experiment_image(
    request, experiment_id, parameterset_id, parameter_name):

    # TODO handle not exist

    if not authz.has_experiment_access(request, experiment_id):
        return return_response_error(request)

    image = ExperimentParameter.objects.get(name__name=parameter_name,
                                            parameterset=parameterset_id)

    return HttpResponse(b64decode(image.string_value), mimetype='image/jpeg')


@authz.dataset_access_required
def display_dataset_image(
    request, dataset_id, parameterset_id, parameter_name):

    # TODO handle not exist

    if not authz.has_dataset_access(request, dataset_id):
        return return_response_error(request)

    image = DatasetParameter.objects.get(name__name=parameter_name,
                                         parameterset=parameterset_id)

    return HttpResponse(b64decode(image.string_value), mimetype='image/jpeg')


@authz.datafile_access_required
def display_datafile_image(
    request, dataset_file_id, parameterset_id, parameter_name):

    # TODO handle not exist

    if not authz.has_datafile_access(request, dataset_file_id):
        return return_response_error(request)

    image = DatafileParameter.objects.get(name__name=parameter_name,
                                          parameterset=parameterset_id)

    return HttpResponse(b64decode(image.string_value), mimetype='image/jpeg')


def about(request):

    c = Context({'subtitle': 'About',
                 'about_pressed': True,
                 'nav': [{'name': 'About', 'link': '/about/'}]})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/about.html', c))


def experiment_index(request):

    experiments = None
    shared_experiments = None

    if request.user.is_authenticated():
        experiments = authz.get_owned_experiments(request)
        if experiments:
            experiments = experiments.order_by('-update_time')

        shared_experiments = authz.get_shared_experiments(request)
        if shared_experiments:
            shared_experiments = shared_experiments.order_by('-update_time')

    public_experiments = Experiment.objects.filter(public=True)
    if public_experiments:
        public_experiments = public_experiments.order_by('-update_time')

    c = Context({
        'experiments': experiments,
        'shared_experiments': shared_experiments,
        'public_experiments': public_experiments,
        'subtitle': 'Experiment Index',
        'bodyclass': 'list',
        'nav': [{'name': 'Data', 'link': '/experiment/view/'}],
        'next': '/experiment/view/',
        'data_pressed': True})

    return HttpResponse(render_response_search(request,
                        'tardis_portal/experiment_index.html', c))


@authz.experiment_access_required
def view_experiment(request, experiment_id):

    """View an existing experiment.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be edited
    :type experiment_id: string
    :rtype: :class:`django.http.HttpResponse`

    """
    c = Context({})

    try:
        experiment = Experiment.safe.get(request, experiment_id)
    except PermissionDenied:
        return return_response_error(request)
    except Experiment.DoesNotExist:
        return return_response_not_found(request)

    c['experiment'] = experiment
    c['has_write_permissions'] = \
        authz.has_write_permissions(request, experiment_id)
    if request.user.is_authenticated():
        c['is_owner'] = authz.has_experiment_ownership(request, experiment_id)
    c['subtitle'] = experiment.title
    c['nav'] = [{'name': 'Data', 'link': '/experiment/view/'},
                {'name': experiment.title,
                 'link': experiment.get_absolute_url()}]

    if 'status' in request.POST:
        c['status'] = request.POST['status']
    if 'error' in request.POST:
        c['error'] = request.POST['error']
    if 'query' in request.GET:
        c['search_query'] = SearchQueryString(request.GET['query'])
    if  'search' in request.GET:
        c['search'] = request.GET['search']
    if  'load' in request.GET:
        c['load'] = request.GET['load']

    import sys
    appnames = []
    appurls = []
    for app in getTardisApps():
        try:
            appnames.append(sys.modules['%s.%s.settings'
                                        % (settings.TARDIS_APP_ROOT, app)].NAME)
            appurls.append('%s.%s.views.index' % (settings.TARDIS_APP_ROOT, app))
        except:
            pass

    c['apps'] = zip(appurls, appnames)

    return HttpResponse(render_response_index(request,
                        'tardis_portal/view_experiment.html', c))


@authz.experiment_access_required
def experiment_description(request, experiment_id):
    """View an existing experiment's description. To be loaded via ajax.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be edited
    :type experiment_id: string
    :rtype: :class:`django.http.HttpResponse`

    """
    c = Context({})

    try:
        experiment = Experiment.safe.get(request, experiment_id)
    except PermissionDenied:
        return return_response_error(request)
    except Experiment.DoesNotExist:
        return return_response_not_found(request)

    c['experiment'] = experiment
    c['subtitle'] = experiment.title
    c['nav'] = [{'name': 'Data', 'link': '/experiment/view/'},
                {'name': experiment.title,
                 'link': experiment.get_absolute_url()}]

    c['authors'] = experiment.author_experiment_set.all()

    c['datafiles'] = \
        Dataset_File.objects.filter(dataset__experiment=experiment_id)

    acl = ExperimentACL.objects.filter(pluginId=django_user,
                                       experiment=experiment,
                                       isOwner=True)

    # TODO: resolve usernames through UserProvider!
    # Right now there are exceptions every time for ldap users..
    c['owners'] = []
    for a in acl:
        try:
            c['owners'].append(User.objects.get(pk=str(a.entityId)))
        except User.DoesNotExist:
            #logger.exception('user for acl %i does not exist' % a.id)
            pass

    # calculate the sum of the datafile sizes
    size = 0
    for df in c['datafiles']:
        try:
            size = size + long(df.size)
        except:
            pass
    c['size'] = size

    c['has_read_or_owner_ACL'] = \
        authz.has_read_or_owner_ACL(request, experiment_id)

    c['has_write_permissions'] = \
        authz.has_write_permissions(request, experiment_id)

    if request.user.is_authenticated():
        c['is_owner'] = authz.has_experiment_ownership(request, experiment_id)

    c['protocol'] = []
    download_urls = experiment.get_download_urls()
    for key, value in download_urls.iteritems():
        c['protocol'] += [[key, value]]

    if 'status' in request.GET:
        c['status'] = request.GET['status']
    if 'error' in request.GET:
        c['error'] = request.GET['error']

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/experiment_description.html', c))
#
# Class to manage switching between space separated search queries and
# '+' separated search queries (for addition to urls
#
# TODO This would probably be better handled with filters
#
class SearchQueryString():

    def __init__(self, query_string):
        import re
        # remove extra spaces around colons
        stripped_query = re.sub('\s*?:\s*', ':', query_string)

        # create a list of terms which can be easily joined by
        # spaces or pluses
        self.query_terms = stripped_query.split()

    def __unicode__(self):
        return ' '.join(self.query_terms)

    def  url_safe_query(self):
        return '+'.join(self.query_terms)

    def query_string(self):
        return self.__unicode__()

@never_cache
@authz.experiment_access_required
def experiment_datasets(request, experiment_id):

    """View a listing of dataset of an existing experiment as ajax loaded tab.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be edited
    :type experiment_id: string
    :param template_name: the path of the template to render
    :type template_name: string
    :rtype: :class:`django.http.HttpResponse`

    """
    c = Context({'upload_complete_url':
                     reverse('tardis.tardis_portal.views.upload_complete'),
                 'searchDatafileSelectionForm':
                     getNewSearchDatafileSelectionForm(),
                 })

    try:
        experiment = Experiment.safe.get(request, experiment_id)
    except PermissionDenied:
        return return_response_error(request)
    except Experiment.DoesNotExist:
        return return_response_not_found(request)

    c['experiment'] = experiment
    if 'query' in request.GET:

        # We've been passed a query to get back highlighted results.
        # Only pass back matching datafiles
        #
        search_query = FacetFixedSearchQuery(backend=HighlightSearchBackend())
        sqs = SearchQuerySet(query=search_query)
        query = SearchQueryString(request.GET['query'])
        facet_counts = sqs.raw_search(query.query_string() + ' AND experiment_id_stored:%i' % (int(experiment_id)), end_offset=1).facet('dataset_id_stored').highlight().facet_counts()
        if facet_counts:
            dataset_id_facets = facet_counts['fields']['dataset_id_stored']
        else:
            dataset_id_facets = []

        c['highlighted_datasets'] = [ int(f[0]) for f in dataset_id_facets ]
        c['file_matched_datasets'] = []
        c['search_query'] = query

        # replace '+'s with spaces
    elif 'datafileResults' in request.session and 'search' in request.GET:
        c['highlighted_datasets'] = None
        c['highlighted_dataset_files'] = [r.pk for r in request.session['datafileResults']]
        c['file_matched_datasets'] = \
            list(set(r.dataset.pk for r in request.session['datafileResults']))
        c['search'] = True

    else:
        c['highlighted_datasets'] = None
        c['highlighted_dataset_files'] = None
        c['file_matched_datasets'] = None

    c['datasets'] = \
         Dataset.objects.filter(experiment=experiment_id)

    c['has_write_permissions'] = \
        authz.has_write_permissions(request, experiment_id)

    c['protocol'] = []
    download_urls = experiment.get_download_urls()
    for key, value in download_urls.iteritems():
        c['protocol'] += [[key, value]]

    if 'status' in request.GET:
        c['status'] = request.GET['status']
    if 'error' in request.GET:
        c['error'] = request.GET['error']

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/experiment_datasets.html', c))


@authz.dataset_access_required
def retrieve_dataset_metadata(request, dataset_id):
    dataset = Dataset.objects.get(pk=dataset_id)
    has_write_permissions = \
        authz.has_write_permissions(request, dataset.experiment.id)

    c = Context({'dataset': dataset, })
    c['has_write_permissions'] = has_write_permissions
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/dataset_metadata.html', c))


@never_cache
@authz.experiment_access_required
def retrieve_experiment_metadata(request, experiment_id):
    experiment = Experiment.objects.get(pk=experiment_id)
    has_write_permissions = \
        authz.has_write_permissions(request, experiment_id)

    c = Context({'experiment': experiment, })
    c['has_write_permissions'] = has_write_permissions
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/experiment_metadata.html', c))

@permission_required('tardis_portal.add_experiment')
@login_required
def create_experiment(request,
                      template_name='tardis_portal/create_experiment.html'):

    """Create a new experiment view.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param template_name: the path of the template to render
    :type template_name: string
    :rtype: :class:`django.http.HttpResponse`

    """

    c = Context({
        'subtitle': 'Create Experiment',
        'user_id': request.user.id,
        })

    staging = get_full_staging_path(
                                request.user.username)
    if staging:
        c['directory_listing'] = staging_traverse(staging)
        c['staging_mount_prefix'] = settings.STAGING_MOUNT_PREFIX

    if request.method == 'POST':
        form = ExperimentForm(request.POST, request.FILES)
        if form.is_valid():
            full_experiment = form.save(commit=False)

            # group/owner assignment stuff, soon to be replaced

            experiment = full_experiment['experiment']
            experiment.created_by = request.user
            for df in full_experiment['dataset_files']:
                if not df.url.startswith(path.sep):
                    df.url = path.join(get_full_staging_path(
                                        request.user.username),
                                        df.url)
            full_experiment.save_m2m()

            # add defaul ACL
            acl = ExperimentACL(experiment=experiment,
                                pluginId=django_user,
                                entityId=str(request.user.id),
                                canRead=True,
                                canWrite=True,
                                canDelete=True,
                                isOwner=True,
                                aclOwnershipType=ExperimentACL.OWNER_OWNED)
            acl.save()

            request.POST = {'status': "Experiment Created."}
            return HttpResponseRedirect(reverse(
                'tardis.tardis_portal.views.view_experiment',
                args=[str(experiment.id)]) + "#created")

        c['status'] = "Errors exist in form."
        c["error"] = 'true'

    else:
        form = ExperimentForm(extra=1)

    c['form'] = form
    c['default_institution'] = settings.DEFAULT_INSTITUTION
    return HttpResponse(render_response_index(request, template_name, c))


@never_cache
@authz.experiment_access_required
def metsexport_experiment(request, experiment_id):

    from os.path import basename
    from django.core.servers.basehttp import FileWrapper
    from tardis.tardis_portal.metsexporter import MetsExporter
    exporter = MetsExporter()
    filename = exporter.export(experiment_id)
    response = HttpResponse(FileWrapper(file(filename)),
                            mimetype='application')
    response['Content-Disposition'] = \
        'attachment; filename="%s"' % basename(filename)
    return response


@login_required
@permission_required('tardis_portal.change_experiment')
@authz.write_permissions_required
def edit_experiment(request, experiment_id,
                      template="tardis_portal/create_experiment.html"):
    """Edit an existing experiment.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be edited
    :type experiment_id: string
    :param template_name: the path of the template to render
    :type template_name: string
    :rtype: :class:`django.http.HttpResponse`

    """
    experiment = Experiment.objects.get(id=experiment_id)

    c = Context({'subtitle': 'Edit Experiment',
                 'user_id': request.user.id,
                 'experiment_id': experiment_id,
              })

    staging = get_full_staging_path(
                                request.user.username)
    if staging:
        c['directory_listing'] = staging_traverse(staging)
        c['staging_mount_prefix'] = settings.STAGING_MOUNT_PREFIX

    if request.method == 'POST':
        form = ExperimentForm(request.POST, request.FILES,
                              instance=experiment, extra=0)
        if form.is_valid():
            full_experiment = form.save(commit=False)
            experiment = full_experiment['experiment']
            experiment.created_by = request.user
            for df in full_experiment['dataset_files']:
                if df.protocol == "staging":
                    df.url = path.join(
                    get_full_staging_path(request.user.username),
                    df.url)
            full_experiment.save_m2m()

            request.POST = {'status': "Experiment Saved."}
            return HttpResponseRedirect(reverse(
                'tardis.tardis_portal.views.view_experiment',
                args=[str(experiment.id)]) + "#saved")

        c['status'] = "Errors exist in form."
        c["error"] = 'true'
    else:
        form = ExperimentForm(instance=experiment, extra=0)

    c['form'] = form

    return HttpResponse(render_response_index(request,
                        template, c))


# todo complete....
def login(request):
    from tardis.tardis_portal.auth import login, auth_service

    if type(request.user) is not AnonymousUser:
        # redirect the user to the home page if he is trying to go to the
        # login page
        return HttpResponseRedirect('/')

    # TODO: put me in SETTINGS
    if 'username' in request.POST and \
            'password' in request.POST:
        authMethod = request.POST['authMethod']

        if 'next' not in request.GET:
            next = '/'
        else:
            next = request.GET['next']

        user = auth_service.authenticate(
            authMethod=authMethod, request=request)

        if user:
            user.backend = 'django.contrib.auth.backends.ModelBackend'
            login(request, user)
            return HttpResponseRedirect(next)

        c = Context({'status': "Sorry, username and password don't match.",
                     'error': True,
                     'loginForm': LoginForm()})
        return HttpResponseForbidden( \
                render_response_index(request, 'tardis_portal/login.html', c))

    c = Context({'loginForm': LoginForm()})

    return HttpResponse(render_response_index(request,
                        'tardis_portal/login.html', c))


@permission_required('tardis_portal.change_userauthentication')
@login_required()
def manage_auth_methods(request):
    '''Manage the user's authentication methods using AJAX.'''
    from tardis.tardis_portal.auth.authentication import add_auth_method, \
        merge_auth_method, remove_auth_method, edit_auth_method, \
        list_auth_methods

    if request.method == 'POST':
        operation = request.POST['operation']
        if operation == 'addAuth':
            return add_auth_method(request)
        elif operation == 'mergeAuth':
            return merge_auth_method(request)
        elif operation == 'removeAuth':
            return remove_auth_method(request)
        else:
            return edit_auth_method(request)
    else:
        # if GET, we'll just give the initial list of auth methods for the user
        return list_auth_methods(request)


# TODO removed username from arguments
@transaction.commit_on_success
def _registerExperimentDocument(filename, created_by, expid=None,
                                owners=[], username=None):
    '''
    Register the experiment document and return the experiment id.

    :param filename: path of the document to parse (METS or notMETS)
    :type filename: string
    :param created_by: a User instance
    :type created_by: :py:class:`django.contrib.auth.models.User`
    :param expid: the experiment ID to use
    :type expid: int
    :param owners: a list of owners
    :type owner: list
    :param username: **UNUSED**
    :rtype: int

    '''

    
    f = open(filename)
    firstline = f.readline()
    f.close()

    if firstline.startswith('<experiment'):
        logger.debug('processing simple xml')
        processExperiment = ProcessExperiment()
        eid = processExperiment.process_simple(filename, created_by, expid)

    else:
        logger.debug('processing METS')
        eid = parseMets(filename, created_by, expid)

    auth_key = ''
    try:
        auth_key = settings.DEFAULT_AUTH
    except AttributeError:
        logger.error('no default authentication for experiment ownership set (settings.DEFAULT_AUTH)')

    force_user_create = False
    try:
        force_user_create = settings.DEFAULT_AUTH_FORCE_USER_CREATE
    except AttributeError:
        pass

    if auth_key:
        for owner in owners:
            # for each PI
            if not owner:
                continue

            owner_username = None
            if '@' in owner:
                owner_username = auth_service.getUsernameByEmail(auth_key,
                                    owner)
            if not owner_username:
                owner_username = owner

            owner_user = auth_service.getUser(auth_key, owner_username,
                      force_user_create=force_user_create)
            # if exist, create ACL
            if owner_user:
                logger.debug('registering owner: ' + owner)
                e = Experiment.objects.get(pk=eid)

                acl = ExperimentACL(experiment=e,
                                    pluginId=django_user,
                                    entityId=str(owner_user.id),
                                    canRead=True,
                                    canWrite=True,
                                    canDelete=True,
                                    isOwner=True,
                                    aclOwnershipType=ExperimentACL.OWNER_OWNED)
                acl.save()

    return eid


# web service
def register_experiment_ws_xmldata(request):

    status = ''
    if request.method == 'POST':  # If the form has been submitted...

        # A form bound to the POST data
        form = RegisterExperimentForm(request.POST, request.FILES)
        if form.is_valid():  # All validation rules pass

            xmldata = request.FILES['xmldata']
            username = form.cleaned_data['username']
            originid = form.cleaned_data['originid']
            from_url = form.cleaned_data['from_url']

            user = auth_service.authenticate(request=request,
                                             authMethod=localdb_auth_key)
            if user:
                if not user.is_active:
                    return return_response_error(request)
            else:
                return return_response_error(request)

            e = Experiment(
                title='Placeholder Title',
                approved=True,
                created_by=user,
                )
            e.save()
            eid = e.id

            filename = path.join(e.get_or_create_directory(),
                                 'mets_upload.xml')
            f = open(filename, 'wb+')
            for chunk in xmldata.chunks():
                f.write(chunk)
            f.close()

            logger.info('=== processing experiment: START')
            owners = request.POST.getlist('experiment_owner')
            try:
                _registerExperimentDocument(filename=filename,
                                            created_by=user,
                                            expid=eid,
                                            owners=owners,
                                            username=username)
                logger.info('=== processing experiment %s: DONE' % eid)
            except:
                logger.exception('=== processing experiment %s: FAILED!' % eid)
                return return_response_error(request)

            if from_url:
                logger.debug('=== sending file request')
                try:
                    file_transfer_url = from_url + '/file_transfer/'
                    data = urlencode({
                            'originid': str(originid),
                            'eid': str(eid),
                            'site_settings_url':
                                request.build_absolute_uri(
                                    '/site-settings.xml/'),
                            })
                    urlopen(file_transfer_url, data)
                    logger.info('=== file-transfer request submitted to %s'
                                % file_transfer_url)
                except:
                    logger.exception('=== file-transfer request to %s FAILED!'
                                     % file_transfer_url)

            response = HttpResponse(str(eid), status=200)
            response['Location'] = request.build_absolute_uri(
                '/experiment/view/' + str(eid))
            return response
    else:
        form = RegisterExperimentForm()  # An unbound form

    c = Context({
        'form': form,
        'status': status,
        'subtitle': 'Register Experiment',
        'searchDatafileSelectionForm': getNewSearchDatafileSelectionForm()})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/register_experiment.html', c))


@never_cache
@authz.datafile_access_required
def retrieve_parameters(request, dataset_file_id):

    parametersets = DatafileParameterSet.objects.all()
    parametersets = parametersets.filter(dataset_file__pk=dataset_file_id)

    experiment_id = Dataset_File.objects.get(id=dataset_file_id).\
        dataset.experiment.id

    has_write_permissions = \
        authz.has_write_permissions(request, experiment_id)

    c = Context({'parametersets': parametersets,
                 'has_write_permissions': has_write_permissions})

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/parameters.html', c))


@never_cache
@authz.dataset_access_required
def retrieve_datafile_list(request, dataset_id, template_name='tardis_portal/ajax/datafile_list.html'):

    params = {}

    query = None
    highlighted_dsf_pks = []

    if 'query' in request.GET:
        search_query = FacetFixedSearchQuery(backend=HighlightSearchBackend())
        sqs = SearchQuerySet(query=search_query)
        query =  SearchQueryString(request.GET['query'])
        results = sqs.raw_search(query.query_string() + ' AND dataset_id_stored:%i' % (int(dataset_id))).load_all()
        highlighted_dsf_pks = [int(r.pk) for r in results if r.model_name == 'dataset_file' and r.dataset_id_stored == int(dataset_id)]

        params['query'] = query.query_string()

    elif 'datafileResults' in request.session and 'search' in request.GET:
        highlighted_dsf_pks = [r.pk for r in request.session['datafileResults']]

    dataset_results = \
        Dataset_File.objects.filter(
            dataset__pk=dataset_id,
        ).order_by('filename')

    if request.GET.get('limit', False) and len(highlighted_dsf_pks):
        dataset_results = \
        dataset_results.filter(pk__in=highlighted_dsf_pks)
        params['limit'] = request.GET['limit']

    filename_search = None

    if 'filename' in request.GET and len(request.GET['filename']):
        filename_search = request.GET['filename']
        dataset_results = \
            dataset_results.filter(url__icontains=filename_search)

        params['filename'] = filename_search

    # pagination was removed by someone in the interface but not here.
    # need to fix.
    pgresults = 100

    paginator = Paginator(dataset_results, pgresults)

    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1

    # If page request (9999) is out of range, deliver last page of results.

    try:
        dataset = paginator.page(page)
    except (EmptyPage, InvalidPage):
        dataset = paginator.page(paginator.num_pages)

    is_owner = False
    has_write_permissions = False

    if request.user.is_authenticated():
        experiment_id = Experiment.objects.get(dataset__id=dataset_id).id
        is_owner = authz.has_experiment_ownership(request, experiment_id)

        has_write_permissions = \
            authz.has_write_permissions(request, experiment_id)

    immutable = Dataset.objects.get(id=dataset_id).immutable

    params = urlencode(params)

    c = Context({
        'dataset': dataset,
        'paginator': paginator,
        'immutable': immutable,
        'dataset_id': dataset_id,
        'filename_search': filename_search,
        'is_owner': is_owner,
        'highlighted_dataset_files': highlighted_dsf_pks,
        'has_write_permissions': has_write_permissions,
        'search_query' : query,
        'params' : params

        })
    return HttpResponse(render_response_index(request, template_name, c))


@login_required()
def control_panel(request):

    experiments = Experiment.safe.owned(request)
    if experiments:
        experiments = experiments.order_by('title')

    c = Context({'experiments': experiments,
                 'subtitle': 'Experiment Control Panel'})

    return HttpResponse(render_response_index(request,
                        'tardis_portal/control_panel.html', c))


@oracle_dbops_hack
def search_experiment(request):
    
    """Either show the search experiment form or the result of the search
    experiment query.

    """

    if len(request.GET) == 0:
        return __forwardToSearchExperimentFormPage(request)

    form = __getSearchExperimentForm(request)
    experiments = __processExperimentParameters(request, form)

    # check if the submitted form is valid
    if experiments is not None:
        bodyclass = 'list'
    else:
        return __forwardToSearchExperimentFormPage(request)

    # remove information from previous searches from session
    if 'datafileResults' in request.session:
        del request.session['datafileResults']

    results = []
    for e in experiments:
        result = {}
        result['sr'] = e
        result['dataset_hit'] = False
        result['dataset_file_hit'] = False
        result['experiment_hit'] = True
        results.append(result)
    c = Context({'header': 'Search Experiment',
                 'experiments': results,
                 'bodyclass': bodyclass})
    url = 'tardis_portal/search_experiment_results.html'
    return HttpResponse(render_response_search(request, url, c))


def search_quick(request):
    get = False
    experiments = Experiment.objects.all().order_by('title')

    if 'results' in request.GET:
        get = True
        if 'quicksearch' in request.GET \
            and len(request.GET['quicksearch']) > 0:
            experiments = \
                experiments.filter(
                title__icontains=request.GET['quicksearch']) | \
                experiments.filter(
                institution_name__icontains=request.GET['quicksearch']) | \
                experiments.filter(
                author_experiment__author__name__icontains=request.GET[
                'quicksearch']) | \
                experiments.filter(
                pdbid__pdbid__icontains=request.GET['quicksearch'])

            experiments = experiments.distinct()

            logger.debug(experiments)

    c = Context({'submitted': get, 'experiments': experiments,
                'subtitle': 'Search Experiments'})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/search_experiment.html', c))


def __getFilteredDatafiles(request, searchQueryType, searchFilterData):
    """Filter the list of datafiles for the provided searchQueryType using the
    cleaned up searchFilterData.

    Arguments:
    request -- the HTTP request
    searchQueryType -- the type of query, 'mx' or 'saxs'
    searchFilterData -- the cleaned up search form data

    Returns:
    A list of datafiles as a result of the query or None if the provided search
      request is invalid

    """

    datafile_results = authz.get_accessible_datafiles_for_user(request)
    logger.info('__getFilteredDatafiles: searchFilterData {0}'.
        format(searchFilterData))

    # there's no need to do any filtering if we didn't find any
    # datafiles that the user has access to
    if not datafile_results:
        logger.info("""__getFilteredDatafiles: user {0} doesn\'t have
                    access to any experiments""".format(request.user))
        return datafile_results

    datafile_results = \
        datafile_results.filter(
datafileparameterset__datafileparameter__name__schema__namespace__in=Schema
    .getNamespaces(
        Schema.DATAFILE, searchQueryType)).distinct()

    # if filename is searchable which i think will always be the case...
    if searchFilterData['filename'] != '':
        datafile_results = \
            datafile_results.filter(
            filename__icontains=searchFilterData['filename'])
    # TODO: might need to cache the result of this later on

    # get all the datafile parameters for the given schema
    parameters = [p for p in
        ParameterName.objects.filter(
        schema__namespace__in=Schema.getNamespaces(Schema.DATAFILE,
        searchQueryType))]

    datafile_results = __filterParameters(parameters, datafile_results,
            searchFilterData, 'datafileparameterset__datafileparameter')

    # get all the dataset parameters for given schema
    parameters = [p for p in
        ParameterName.objects.filter(
        schema__namespace__in=Schema.getNamespaces(Schema.DATASET,
        searchQueryType))]

    datafile_results = __filterParameters(parameters, datafile_results,
            searchFilterData, 'dataset__datasetparameterset__datasetparameter')

    # let's sort it in the end

    if datafile_results:
        datafile_results = datafile_results.order_by('filename')
    logger.debug("results: {0}".format(datafile_results))
    return datafile_results


def __getFilteredExperiments(request, searchFilterData):
    """Filter the list of experiments using the cleaned up searchFilterData.

    Arguments:
    request -- the HTTP request
    searchFilterData -- the cleaned up search experiment form data

    Returns:
    A list of experiments as a result of the query or None if the provided
      search request is invalid

    """

    experiments = authz.get_accessible_experiments(request)

    if experiments is None:
        return []

    # search for the default experiment fields
    if searchFilterData['title'] != '':
        experiments = \
            experiments.filter(title__icontains=searchFilterData['title'])

    if searchFilterData['description'] != '':
        experiments = \
            experiments.filter(
            description__icontains=searchFilterData['description'])

    if searchFilterData['institutionName'] != '':
        experiments = \
            experiments.filter(
            institution_name__icontains=searchFilterData['institutionName'])

    if searchFilterData['creator'] != '':
        experiments = \
            experiments.filter(
            author_experiment__author__icontains=searchFilterData['creator'])

    date = searchFilterData['date']
    if not date == None:
        experiments = \
            experiments.filter(start_time__lt=date, end_time__gt=date)

    # get all the experiment parameters
    exp_schema_namespaces = Schema.getNamespaces(Schema.EXPERIMENT)
    parameters = ParameterName.objects.filter(
        schema__namespace__in=exp_schema_namespaces, is_searchable=True)

    experiments = __filterParameters(parameters, experiments,
            searchFilterData, 'experimentparameterset__experimentparameter')

    # let's sort it in the end
    experiments = experiments.order_by('title')

    return experiments


def __filterParameters(parameters, datafile_results,
                       searchFilterData, paramType):
    """Go through each parameter and apply it as a filter (together with its
    specified comparator) on the provided list of datafiles.

    :param parameters: list of ParameterNames model
    :type parameters: list containing
       :py:class:`tardis.tardis_portal.models.ParameterNames`
    :param datafile_results: list of datafile to apply the filter
    :param searchFilterData: the cleaned up search form data
    :param paramType: either ``datafile`` or ``dataset``
    :type paramType: :py:class:`tardis.tardis_portal.models.Dataset` or
       :py:class:`tardis.tardis_portal.models.Dataset_File`

    :returns: A list of datafiles as a result of the query or None if the
      provided search request is invalid

    """

    for parameter in parameters:
        fieldName = parameter.getUniqueShortName()
        kwargs = {paramType + '__name__id': parameter.id}
        try:

            # if parameter is a string...
            if not parameter.data_type == ParameterName.NUMERIC:
                if searchFilterData[fieldName] != '':
                    # let's check if this is a field that's specified to be
                    # displayed as a dropdown menu in the form
                    if parameter.choices != '':
                        if searchFilterData[fieldName] != '-':
                            kwargs[paramType + '__string_value__iexact'] = \
                                searchFilterData[fieldName]
                    else:
                        if parameter.comparison_type == \
                                ParameterName.EXACT_VALUE_COMPARISON:
                            kwargs[paramType + '__string_value__iexact'] = \
                                searchFilterData[fieldName]
                        elif parameter.comparison_type == \
                                ParameterName.CONTAINS_COMPARISON:
                            # we'll implement exact comparison as 'icontains'
                            # for now
                            kwargs[paramType + '__string_value__icontains'] = \
                                searchFilterData[fieldName]
                        else:
                            # if comparison_type on a string is a comparison
                            # type that can only be applied to a numeric value,
                            # we'll default to just using 'icontains'
                            # comparison
                            kwargs[paramType + '__string_value__icontains'] = \
                                searchFilterData[fieldName]
                else:
                    pass
            else:  # parameter.isNumeric():
                if parameter.comparison_type == \
                        ParameterName.RANGE_COMPARISON:
                    fromParam = searchFilterData[fieldName + 'From']
                    toParam = searchFilterData[fieldName + 'To']
                    if fromParam is None and toParam is None:
                        pass
                    else:
                        # if parameters are provided and we want to do a range
                        # comparison
                        # note that we're using '1' as the lower range as using
                        # '0' in the filter would return all the data
                        # TODO: investigate on why the oddness above is
                        #       happening
                        # TODO: we should probably move the static value here
                        #       to the constants module
                        kwargs[paramType + '__numerical_value__range'] = \
                            (fromParam is None and
                             constants.FORM_RANGE_LOWEST_NUM or fromParam,
                             toParam is not None and toParam or
                             constants.FORM_RANGE_HIGHEST_NUM)

                elif searchFilterData[fieldName] is not None:

                    # if parameter is an number and we want to handle other
                    # type of number comparisons
                    if parameter.comparison_type == \
                            ParameterName.EXACT_VALUE_COMPARISON:
                        kwargs[paramType + '__numerical_value__exact'] = \
                            searchFilterData[fieldName]

                    # TODO: is this really how not equal should be declared?
                    # elif parameter.comparison_type ==
                    #       ParameterName.NOT_EQUAL_COMPARISON:
                    #   datafile_results = \
                    #       datafile_results.filter(
                    #  datafileparameter__name__name__icontains=parameter.name)
                    #       .filter(
                    #  ~Q(datafileparameter__numerical_value=searchFilterData[
                    #       parameter.name]))

                    elif parameter.comparison_type == \
                            ParameterName.GREATER_THAN_COMPARISON:
                        kwargs[paramType + '__numerical_value__gt'] = \
                            searchFilterData[fieldName]
                    elif parameter.comparison_type == \
                            ParameterName.GREATER_THAN_EQUAL_COMPARISON:
                        kwargs[paramType + '__numerical_value__gte'] = \
                            searchFilterData[fieldName]
                    elif parameter.comparison_type == \
                            ParameterName.LESS_THAN_COMPARISON:
                        kwargs[paramType + '__numerical_value__lt'] = \
                            searchFilterData[fieldName]
                    elif parameter.comparison_type == \
                            ParameterName.LESS_THAN_EQUAL_COMPARISON:
                        kwargs[paramType + '__numerical_value__lte'] = \
                            searchFilterData[fieldName]
                    else:
                        # if comparison_type on a numeric is a comparison type
                        # that can only be applied to a string value, we'll
                        # default to just using 'exact' comparison
                        kwargs[paramType + '__numerical_value__exact'] = \
                            searchFilterData[fieldName]
                else:
                    # ignore...
                    pass

            # we will only update datafile_results if we have an additional
            # filter (based on the 'passed' condition) in addition to the
            # initial value of kwargs
            if len(kwargs) > 1:
                logger.debug(kwargs)
                datafile_results = datafile_results.filter(**kwargs)
        except KeyError:
            pass

    return datafile_results


def __forwardToSearchDatafileFormPage(request, searchQueryType,
        searchForm=None):
    """Forward to the search data file form page."""

    # TODO: remove this later on when we have a more generic search form
    if searchQueryType == 'mx':
        url = 'tardis_portal/search_datafile_form_mx.html'
        searchForm = MXDatafileSearchForm()
        c = Context({'header': 'Search Datafile',
                     'searchForm': searchForm})
        return HttpResponse(render_response_search(request, url, c))

    url = 'tardis_portal/search_datafile_form.html'
    if not searchForm:
        #if searchQueryType == 'saxs':
        SearchDatafileForm = createSearchDatafileForm(searchQueryType)
        searchForm = SearchDatafileForm()
        #else:
        #    # TODO: what do we need to do if the user didn't provide a page to
        #            display?
        #    pass

    from itertools import groupby

    # sort the fields in the form as it will make grouping the related fields
    # together in the next step easier
    sortedSearchForm = sorted(searchForm, lambda x, y: cmp(x.name, y.name))

    # modifiedSearchForm will be used to customise how the range type of fields
    # will be displayed. range type of fields will be displayed side by side.
    modifiedSearchForm = [list(g) for k, g in groupby(
        sortedSearchForm, lambda x: x.name.rsplit('To')[0].rsplit('From')[0])]

    # the searchForm will be used by custom written templates whereas the
    # modifiedSearchForm will be used by the 'generic template' that the
    # dynamic search datafiles form uses.
    c = Context({'header': 'Search Datafile',
                 'searchForm': searchForm,
                 'modifiedSearchForm': modifiedSearchForm})
    return HttpResponse(render_response_search(request, url, c))


def __forwardToSearchExperimentFormPage(request):
    """Forward to the search experiment form page."""

    searchForm = __getSearchExperimentForm(request)

    c = Context({'searchForm': searchForm})
    url = 'tardis_portal/search_experiment_form.html'
    return HttpResponse(render_response_search(request, url, c))


def __getSearchDatafileForm(request, searchQueryType):
    """Create the search datafile form based on the HTTP GET request.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param searchQueryType: The search query type: 'mx' or 'saxs'
    :raises:
       :py:class:`tardis.tardis_portal.errors.UnsupportedSearchQueryTypeError`
       is the provided searchQueryType is not supported.
    :returns: The supported search datafile form

    """

    try:
        SearchDatafileForm = createSearchDatafileForm(searchQueryType)
        form = SearchDatafileForm(request.GET)
        return form
    except UnsupportedSearchQueryTypeError, e:
        raise e


def __getSearchExperimentForm(request):
    """Create the search experiment form.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :returns: The search experiment form.

    """

    SearchExperimentForm = createSearchExperimentForm()
    form = SearchExperimentForm(request.GET)
    return form


def __processDatafileParameters(request, searchQueryType, form):
    """Validate the provided datafile search request and return search results.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param searchQueryType: The search query type
    :param form: The search form to use
    :raises:
       :py:class:`tardis.tardis_portal.errors.SearchQueryTypeUnprovidedError`
       if searchQueryType is not in the HTTP GET request
    :raises:
       :py:class:`tardis.tardis_portal.errors.UnsupportedSearchQueryTypeError`
       is the provided searchQueryType is not supported
    :returns: A list of datafiles as a result of the query or None if the
       provided search request is invalid.
    :rtype: list of :py:class:`tardis.tardis_portal.models.Dataset_Files` or
       None

    """

    if form.is_valid():

        datafile_results = __getFilteredDatafiles(request,
            searchQueryType, form.cleaned_data)

        # let's cache the query with all the filters in the session so
        # we won't have to keep running the query all the time it is needed
        # by the paginator
        request.session['datafileResults'] = datafile_results
        return datafile_results
    else:
        return None


def __processExperimentParameters(request, form):
    """Validate the provided experiment search request and return search
    results.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param form: The search form to use
    :returns: A list of experiments as a result of the query or None if the
      provided search request is invalid.

    """

    if form.is_valid():
        experiments = __getFilteredExperiments(request, form.cleaned_data)
        # let's cache the query with all the filters in the session so
        # we won't have to keep running the query all the time it is needed
        # by the paginator
        request.session['experiments'] = experiments
        return experiments
    else:
        return None


def search_datafile(request):
    """Either show the search datafile form or the result of the search
    datafile query.

    """

    if 'type' in request.GET:
        searchQueryType = request.GET.get('type')
    else:
        # for now we'll default to MX if nothing is provided
        # TODO: should we forward the page to experiment search page if
        #       nothing is provided in the future?
        searchQueryType = 'mx'
    logger.info('search_datafile: searchQueryType {0}'.format(searchQueryType))
    # TODO: check if going to /search/datafile will flag an error in unit test
    bodyclass = None

    if 'page' not in request.GET and 'type' in request.GET and \
            len(request.GET) > 1:
        # display the 1st page of the results

        form = __getSearchDatafileForm(request, searchQueryType)
        datafile_results = __processDatafileParameters(
            request, searchQueryType, form)
        if datafile_results is not None:
            bodyclass = 'list'
        else:
            return __forwardToSearchDatafileFormPage(
                request, searchQueryType, form)

    else:
        if 'page' in request.GET:
            # succeeding pages of pagination
            if 'datafileResults' in request.session:
                datafile_results = request.session['datafileResults']
            else:
                form = __getSearchDatafileForm(request, searchQueryType)
                datafile_results = __processDatafileParameters(request,
                    searchQueryType, form)
                if datafile_results is not None:
                    bodyclass = 'list'
                else:
                    return __forwardToSearchDatafileFormPage(request,
                        searchQueryType, form)
        else:
            # display the form
            if 'datafileResults' in request.session:
                del request.session['datafileResults']
            return __forwardToSearchDatafileFormPage(request, searchQueryType)

    # process the files to be displayed by the paginator...
    #paginator = Paginator(datafile_results,
    #                      constants.DATAFILE_RESULTS_PER_PAGE)

    #try:
    #    page = int(request.GET.get('page', '1'))
    #except ValueError:
    #    page = 1

    # If page request (9999) is out of :range, deliver last page of results.
    #try:
    #    datafiles = paginator.page(page)
    #except (EmptyPage, InvalidPage):
    #    datafiles = paginator.page(paginator.num_pages)

    import re
    cleanedUpQueryString = re.sub('&page=\d+', '',
        request.META['QUERY_STRING'])

    # get experiments associated with datafiles
    if datafile_results:
        experiment_pks = list(set(datafile_results.values_list('dataset__experiment', flat=True)))
        experiments = Experiment.safe.in_bulk(experiment_pks)
    else:
        experiments = {}

    results = []
    for key, e in experiments.items():
        result = {}
        result['sr'] = e
        result['dataset_hit'] = False
        result['dataset_file_hit'] = True
        result['experiment_hit'] = False
        results.append(result)

    c = Context({
        'experiments': results,
        'datafiles': datafile_results,
        #'paginator': paginator,
        'query_string': cleanedUpQueryString,
        'subtitle': 'Search Datafiles',
        'nav': [{'name': 'Search Datafile', 'link': '/search/datafile/'}],
        'bodyclass': bodyclass,
        'search_pressed': True,
        'searchDatafileSelectionForm': getNewSearchDatafileSelectionForm()})
    url = 'tardis_portal/search_experiment_results.html'
    return HttpResponse(render_response_search(request, url, c))


@never_cache
@login_required()
def retrieve_user_list(request):
    # TODO: Hook this up to authservice.searchUsers() to actually get
    # autocompletion data directly from auth backends.
    # The following local DB query would be moved to auth.localdb_auth.SearchUsers.
    query = request.GET.get('q', '')
    limit = int(request.GET.get('limit', '10'))

    # Search all user fields and also the UserAuthentication username.
    q = Q(username__icontains=query)   | \
        Q(email__icontains=query) | \
        Q(userprofile__userauthentication__username__icontains=query)

    # Tokenize query string so "Bob Sm" matches (first_name~=Bob & last_name~=Smith).
    tokens = query.split()
    if len(tokens) < 2:
        q |= Q(first_name__icontains=query.strip())
        q |= Q(last_name__icontains=query.strip())
    else:
        q |= Q(first_name__icontains=' '.join(tokens[:-1])) & Q(last_name__icontains=tokens[-1])

    q_tokenuser = Q(username=settings.TOKEN_USERNAME)
    users_query = User.objects.exclude(q_tokenuser).filter(q).distinct().select_related('userprofile')

    # HACK FOR ORACLE - QUERY GENERATED DOES NOT WORK WITH LIMIT SO USING ITERATOR INSTEAD
    from itertools import islice
    first_n_users = list(islice(users_query, limit))

    user_auths = list(UserAuthentication.objects.filter(userProfile__user__in=first_n_users))
    auth_methods = dict( (ap[0], ap[1]) for ap in settings.AUTH_PROVIDERS)
    """
    users = [ {
        "username": "ksr",
        "first_name": "Kieran",
        "last_name": "Spear",
        "email": "email@address.com",
        "auth_methods": [ "ksr:vbl:VBL", "ksr:localdb:Local DB" ]
    } , ... ]
    """
    users = []
    for u in users_query:
        fields = ('first_name', 'last_name', 'username', 'email')
        # Convert attributes to dictionary keys and make sure all values
        # are strings.
        user = dict( [ (k, str(getattr(u, k))) for k in fields ] )
        try:
            user['auth_methods'] = [ '%s:%s:%s' % \
                    (ua.username, ua.authenticationMethod, \
                    auth_methods[ua.authenticationMethod]) \
                    for ua in user_auths if ua.userProfile == u.get_profile() ]
        except UserProfile.DoesNotExist:
            user['auth_methods'] = []

        if not user['auth_methods']:
            user['auth_methods'] = [ '%s:localdb:%s' % \
                    (u.username, auth_methods['localdb']) ]
        users.append(user)

    users.sort(key=itemgetter('first_name'))
    return HttpResponse(json.dumps(users))


@never_cache
@login_required()
def retrieve_group_list(request):

    grouplist = ' ~ '.join(map(str, Group.objects.all().order_by('name')))
    return HttpResponse(grouplist)

def retrieve_field_list(request):

    from tardis.tardis_portal.search_indexes import DatasetFileIndex

    # Get all of the fields in the indexes
    #
    # TODO: these should be onl read from registered indexes
    #
    allFields = DatasetFileIndex.fields.items()

    users = User.objects.all()

    usernames = [u.first_name + ' ' + u.last_name + ':username' for u in users]

    # Collect all of the indexed (searchable) fields, except
    # for the main search document ('text')
    searchableFields = ([key + ':search_field' for key,f in allFields if f.indexed == True and key != 'text' ])

    auto_list = usernames + searchableFields

    fieldList = '+'.join([str(fn) for fn in auto_list])
    return HttpResponse(fieldList)

@never_cache
@authz.experiment_ownership_required
def retrieve_access_list_user(request, experiment_id):
    from tardis.tardis_portal.forms import AddUserPermissionsForm
    user_acls = Experiment.safe.user_acls(request, experiment_id)

    c = Context({ 'user_acls': user_acls, 'experiment_id': experiment_id,
                 'addUserPermissionsForm': AddUserPermissionsForm() })
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/access_list_user.html', c))


@never_cache
@authz.experiment_ownership_required
def retrieve_access_list_group(request, experiment_id):

    from tardis.tardis_portal.forms import AddGroupPermissionsForm

    user_owned_groups = Experiment.safe.user_owned_groups(request,
                                                          experiment_id)
    system_owned_groups = Experiment.safe.system_owned_groups(request,
                                                            experiment_id)

    c = Context({'user_owned_groups': user_owned_groups,
                 'system_owned_groups': system_owned_groups,
                 'experiment_id': experiment_id,
                 'addGroupPermissionsForm': AddGroupPermissionsForm()})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/access_list_group.html', c))


@never_cache
@authz.experiment_ownership_required
def retrieve_access_list_external(request, experiment_id):

    groups = Experiment.safe.external_users(request, experiment_id)
    c = Context({'groups': groups, 'experiment_id': experiment_id})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/access_list_external.html', c))

@never_cache
@authz.experiment_ownership_required
def retrieve_access_list_tokens(request, experiment_id):
    tokens = Token.objects.filter(experiment=experiment_id)
    tokens = [{'expiry_date': token.expiry_date,
                 'user': token.user,
                 'url': request.build_absolute_uri(token.get_absolute_url()),
                 'id': token.id,
                 'experiment_id': experiment_id,
              } for token in tokens]
    c = Context({'tokens': tokens})
    return HttpResponse(render_response_index(request,
        'tardis_portal/ajax/access_list_tokens.html', c))


@never_cache
@authz.group_ownership_required
def retrieve_group_userlist(request, group_id):

    from tardis.tardis_portal.forms import ManageGroupPermissionsForm
    users = User.objects.filter(groups__id=group_id)
    c = Context({'users': users, 'group_id': group_id,
                 'manageGroupPermissionsForm': ManageGroupPermissionsForm()})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/group_user_list.html', c))


@never_cache
@permission_required('auth.change_group')
@login_required()
def manage_groups(request):

    groups = Group.objects.filter(groupadmin__user=request.user)
    c = Context({'groups': groups})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/manage_group_members.html', c))


@never_cache
@authz.group_ownership_required
def add_user_to_group(request, group_id, username):

    if username == settings.TOKEN_USERNAME:
        return HttpResponse('User does not exist: %s' % username)

    authMethod = localdb_auth_key
    isAdmin = False

    if 'isAdmin' in request.GET:
        if request.GET['isAdmin'] == 'true':
            isAdmin = True

    try:
        authMethod = request.GET['authMethod']
        if authMethod == localdb_auth_key:
            user = User.objects.get(username=username)
        else:
            user = UserAuthentication.objects.get(username=username,
                authenticationMethod=authMethod).userProfile.user
    except User.DoesNotExist:
        return return_response_error(request)
    except UserAuthentication.DoesNotExist:
        return return_response_error(request)

    try:
        group = Group.objects.get(pk=group_id)
    except Group.DoesNotExist:
        return HttpResponse('Group does not exist.')

    if user.groups.filter(name=group.name).count() > 0:
        return HttpResponse('User %s is already member of that group.'
                            % username)

    user.groups.add(group)
    user.save()

    if isAdmin:
        groupadmin = GroupAdmin(user=user, group=group)
        groupadmin.save()

    c = Context({'user': user, 'group_id': group_id, 'isAdmin': isAdmin})
    return HttpResponse(render_response_index(request,
         'tardis_portal/ajax/add_user_to_group_result.html', c))


@never_cache
@authz.group_ownership_required
def remove_user_from_group(request, group_id, username):

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return HttpResponse('User %s does not exist.' % username)
    try:
        group = Group.objects.get(pk=group_id)
    except Group.DoesNotExist:
        return HttpResponse('Group does not exist.')

    if user.groups.filter(name=group.name).count() == 0:
        return HttpResponse('User %s is not member of that group.'
                            % username)

    if request.user == user:
        return HttpResponse('You cannot remove yourself from that group.')

    user.groups.remove(group)
    user.save()

    try:
        groupadmin = GroupAdmin.objects.filter(user=user, group=group)
        groupadmin.delete()
    except GroupAdmin.DoesNotExist:
        pass

    return HttpResponse('OK')


@never_cache
@transaction.commit_on_success
@authz.experiment_ownership_required
def add_experiment_access_user(request, experiment_id, username):

    canRead = False
    canWrite = False
    canDelete = False

    if 'canRead' in request.GET:
        if request.GET['canRead'] == 'true':
            canRead = True

    if 'canWrite' in request.GET:
        if request.GET['canWrite'] == 'true':
            canWrite = True

    if 'canDelete' in request.GET:
        if request.GET['canDelete'] == 'true':
            canDelete = True

    authMethod = request.GET['authMethod']
    user = auth_service.getUser(authMethod, username)
    if user is None or username == settings.TOKEN_USERNAME:
        return HttpResponse('User %s does not exist.' % (username))

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return HttpResponse('Experiment (id=%d) does not exist.'
            % (experiment.id))

    acl = ExperimentACL.objects.filter(
        experiment=experiment,
        pluginId=django_user,
        entityId=str(user.id),
        aclOwnershipType=ExperimentACL.OWNER_OWNED)

    if acl.count() == 0:
        acl = ExperimentACL(experiment=experiment,
                            pluginId=django_user,
                            entityId=str(user.id),
                            canRead=canRead,
                            canWrite=canWrite,
                            canDelete=canDelete,
                            aclOwnershipType=ExperimentACL.OWNER_OWNED)

        acl.save()
        c = Context({'authMethod': authMethod,
                     'user': user,
                     'user_acl': acl,
                     'username': username,
                     'experiment_id': experiment_id})

        return HttpResponse(render_response_index(request,
            'tardis_portal/ajax/add_user_result.html', c))

    return HttpResponse('User already has experiment access.')


@never_cache
@authz.experiment_ownership_required
def remove_experiment_access_user(request, experiment_id, username):
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return HttpResponse('User %s does not exist' % username)

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return HttpResponse('Experiment does not exist')

    acl = ExperimentACL.objects.filter(
        experiment=experiment,
        pluginId=django_user,
        entityId=str(user.id),
        aclOwnershipType=ExperimentACL.OWNER_OWNED)

    if acl.count() == 1:
        if int(acl[0].entityId) == request.user.id:
            return HttpResponse('Cannot remove your own user access.')

        acl[0].delete()
        return HttpResponse('OK')
    elif acl.count() == 0:
        return HttpResponse(
            'The user %s does not have access to this experiment.' % username)
    else:
        return HttpResponse('Multiple ACLs found')


@never_cache
@authz.experiment_ownership_required
def change_user_permissions(request, experiment_id, username):

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return return_response_error(request)

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return return_response_error(request)

    try:
        acl = ExperimentACL.objects.get(
            experiment=experiment,
            pluginId=django_user,
            entityId=str(user.id),
            aclOwnershipType=ExperimentACL.OWNER_OWNED)
    except ExperimentACL.DoesNotExist:
        return return_response_error(request)

    if request.method == 'POST':
        form = ChangeUserPermissionsForm(request.POST, instance=acl)

        if form.is_valid:
            form.save()
            url = reverse('tardis.tardis_portal.views.control_panel')
            return HttpResponseRedirect(url)

    else:
        form = ChangeUserPermissionsForm(instance=acl)
        c = Context({'form': form,
                     'header':
                         "Change User Permissions for '%s'" % user.username})

    return HttpResponse(render_response_index(request,
                            'tardis_portal/form_template.html', c))


@never_cache
@authz.experiment_ownership_required
def change_group_permissions(request, experiment_id, group_id):

    try:
        group = Group.objects.get(pk=group_id)
    except Group.DoesNotExist:
        return return_response_error(request)

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return return_response_error(request)

    try:
        acl = ExperimentACL.objects.get(
            experiment=experiment,
            pluginId=django_group,
            entityId=str(group.id),
            aclOwnershipType=ExperimentACL.OWNER_OWNED)
    except ExperimentACL.DoesNotExist:
        return return_response_error(request)

    if request.method == 'POST':
        form = ChangeGroupPermissionsForm(request.POST)

        if form.is_valid():
            acl.canRead = form.cleaned_data['canRead']
            acl.canWrite = form.cleaned_data['canWrite']
            acl.canDelete = form.cleaned_data['canDelete']
            acl.effectiveDate = form.cleaned_data['effectiveDate']
            acl.expiryDate = form.cleaned_data['expiryDate']
            acl.save()
            return HttpResponseRedirect('/experiment/control_panel/')

    else:
        form = ChangeGroupPermissionsForm(
            initial={'canRead': acl.canRead,
                     'canWrite': acl.canWrite,
                     'canDelete': acl.canDelete,
                     'effectiveDate': acl.effectiveDate,
                     'expiryDate': acl.expiryDate})

    c = Context({'form': form,
                 'header': "Change Group Permissions for '%s'" % group.name})

    return HttpResponse(render_response_index(request,
                            'tardis_portal/form_template.html', c))


@never_cache
@transaction.commit_manually
@authz.experiment_ownership_required
def add_experiment_access_group(request, experiment_id, groupname):

    create = False
    canRead = False
    canWrite = False
    canDelete = False
    authMethod = localdb_auth_key
    admin = None

    if 'canRead' in request.GET:
        if request.GET['canRead'] == 'true':
            canRead = True

    if 'canWrite' in request.GET:
        if request.GET['canWrite'] == 'true':
            canWrite = True

#    if 'canDelete' in request.GET:
#        if request.GET['canDelete'] == 'true':
#            canDelete = True

    if 'admin' in request.GET:
        admin = request.GET['admin']

    if 'create' in request.GET:
        if request.GET['create'] == 'true':
            create = True

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        transaction.rollback()
        return HttpResponse('Experiment (id=%d) does not exist' %
                            (experiment_id))

    if create:
        try:
            group = Group(name=groupname)
            group.save()
        except:
            transaction.rollback()
            return HttpResponse('Could not create group %s ' \
            '(It is likely that it already exists)' % (groupname))
    else:
        try:
            group = Group.objects.get(name=groupname)
        except Group.DoesNotExist:
            transaction.rollback()
            return HttpResponse('Group %s does not exist' % (groupname))

    acl = ExperimentACL.objects.filter(
        experiment=experiment,
        pluginId=django_group,
        entityId=str(group.id),
        aclOwnershipType=ExperimentACL.OWNER_OWNED)

    if acl.count() > 0:
        # An ACL already exists for this experiment/group.
        transaction.rollback()
        return HttpResponse('Could not create group %s ' \
            '(It is likely that it already exists)' % (groupname))

    acl = ExperimentACL(experiment=experiment,
                        pluginId=django_group,
                        entityId=str(group.id),
                        canRead=canRead,
                        canWrite=canWrite,
                        canDelete=canDelete,
                        aclOwnershipType=ExperimentACL.OWNER_OWNED)
    acl.save()

    adminuser = None
    if admin:
        if admin == settings.TOKEN_USERNAME:
            transaction.rollback()
            return HttpResponse('User %s does not exist' % (settings.TOKEN_USERNAME))
        try:
            authMethod = request.GET['authMethod']
            if authMethod == localdb_auth_key:
                adminuser = User.objects.get(username=admin)
            else:
                adminuser = UserAuthentication.objects.get(username=admin,
                    authenticationMethod=authMethod).userProfile.user

        except User.DoesNotExist:
            transaction.rollback()
            return HttpResponse('User %s does not exist' % (admin))
        except UserAuthentication.DoesNotExist:
            transaction.rollback()
            return HttpResponse('User %s does not exist' % (admin))

        # create admin for this group and add it to the group
        groupadmin = GroupAdmin(user=adminuser, group=group)
        groupadmin.save()

        adminuser.groups.add(group)
        adminuser.save()

    # add the current user as admin as well for newly created groups
    if create and not request.user == adminuser:
        user = request.user

        groupadmin = GroupAdmin(user=user, group=group)
        groupadmin.save()

        user.groups.add(group)
        user.save()

    c = Context({'group': group,
                 'experiment_id': experiment_id})
    response = HttpResponse(render_response_index(request,
        'tardis_portal/ajax/add_group_result.html', c))
    transaction.commit()
    return response


@never_cache
@authz.experiment_ownership_required
def remove_experiment_access_group(request, experiment_id, group_id):

    try:
        group = Group.objects.get(pk=group_id)
    except Group.DoesNotExist:
        return HttpResponse('Group does not exist')

    try:
        experiment = Experiment.objects.get(pk=experiment_id)
    except Experiment.DoesNotExist:
        return HttpResponse('Experiment does not exist')

    acl = ExperimentACL.objects.filter(
        experiment=experiment,
        pluginId=django_group,
        entityId=str(group.id),
        aclOwnershipType=ExperimentACL.OWNER_OWNED)

    if acl.count() == 1:
        acl[0].delete()
        return HttpResponse('OK')
    elif acl.count() == 0:
        return HttpResponse('No ACL available.'
                            'It is likely the group doesnt have access to'
                            'this experiment.')
    else:
        return HttpResponse('Multiple ACLs found')

    return HttpResponse('')


def stats(request):

    # stats
    public_datafiles = 0
    #public_datafiles = Dataset_File.objects.filter()
    public_experiments = Experiment.objects.filter()

    size = 0
#    for df in public_datafiles:
#        try:
#            size = size + long(df.size)
#        except:
#            pass

    public_datafile_size = size

    # using count() is more efficient than using len() on a query set
    c = Context({'public_datafiles': public_datafiles,
                'public_experiments': public_experiments.count(),
                'public_datafile_size': public_datafile_size})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/stats.html', c))


def import_params(request):
    if request.method == 'POST':  # If the form has been submitted...

        # A form bound to the POST data
        form = ImportParamsForm(request.POST, request.FILES)
        if form.is_valid():  # All validation rules pass

            params = request.FILES['params']
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']

            from django.contrib.auth import authenticate
            user = authenticate(username=username, password=password)
            if user is not None:
                if not user.is_active or not user.is_staff:
                    return return_response_error(request)
            else:
                return return_response_error(request)

            i = 0
            for line in params:
                if i == 0:
                    prefix = line
                    logger.debug(prefix)
                elif i == 1:
                    schema = line
                    logger.debug(schema)

                    try:
                        Schema.objects.get(namespace=schema)
                        return HttpResponse('Schema already exists.')
                    except Schema.DoesNotExist:
                        schema_db = Schema(namespace=schema)
                        # TODO: add the extra info that the Schema instance
                        #       needs
                        schema_db.save()
                else:
                    part = line.split('^')
                    if len(part) == 4:

                        is_numeric = False
                        if part[3].strip(' \n\r') == 'True':
                            is_numeric = True
                        if is_numeric:
                            pn = ParameterName(schema=schema_db,
                                               name=part[0], full_name=part[1],
                                               units=part[2],
                                               data_type=ParameterName.NUMERIC)
                        else:

                            pn = ParameterName(schema=schema_db,
                                               name=part[0], full_name=part[1],
                                               units=part[2],
                                               data_type=ParameterName.STRING)
                        pn.save()

                i = i + 1

            return HttpResponse('OK')
    else:
        form = ImportParamsForm()

    c = Context({'form': form, 'header': 'Import Parameters'})
    return HttpResponse(render_response_index(request,
                        'tardis_portal/form_template.html', c))


def upload_complete(request,
                    template_name='tardis_portal/upload_complete.html'):
    """
    The ajax-loaded result of a file being uploaded

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param template_name: the path of the template to render
    :type template_name: string
    :rtype: :class:`django.http.HttpResponse`
    """

    c = Context({
        'numberOfFiles': request.POST['filesUploaded'],
        'bytes': request.POST['allBytesLoaded'],
        'speed': request.POST['speed'],
        'errorCount': request.POST['errorCount'],
        })
    return render_to_response(template_name, c)


@authz.upload_auth
@authz.dataset_write_permissions_required
def upload(request, dataset_id):
    """
    Uploads a datafile to the store and datafile metadata

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param dataset_id: the dataset_id
    :type dataset_id: integer
    :returns: boolean true if successful
    :rtype: bool
    """

    dataset = Dataset.objects.get(id=dataset_id)

    logger.debug('called upload')
    if request.method == 'POST':
        logger.debug('got POST')
        if request.FILES:

            uploaded_file_post = request.FILES['Filedata']

            filepath = write_uploaded_file_to_dataset(dataset,
                    uploaded_file_post)

            add_datafile_to_dataset(dataset, filepath,
                                    uploaded_file_post.size)

    return HttpResponse('True')

@authz.dataset_write_permissions_required
def upload_files(request, dataset_id,
                 template_name='tardis_portal/ajax/upload_files.html'):
    """
    Creates an Uploadify 'create files' button with a dataset
    destination. `A workaround for a JQuery Dialog conflict\
    <http://www.uploadify.com/forums/discussion/3348/
        uploadify-in-jquery-ui-dialog-modal-causes-double-queue-item/p1>`_

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param template_name: the path of the template to render
    :param dataset_id: the dataset_id
    :type dataset_id: integer
    :returns: A view containing an Uploadify *create files* button
    """
    if 'message' in request.GET:
        message = request.GET['message']
    else:
        message = "Upload Files to Dataset"
    url = reverse('tardis.tardis_portal.views.upload_complete')
    c = Context({'upload_complete_url': url,
                 'dataset_id': dataset_id,
                 'message': message,
                 'session_id': request.session.session_key
                 })
    return render_to_response(template_name, c)


@login_required
def edit_experiment_par(request, parameterset_id):
    parameterset = ExperimentParameterSet.objects.get(id=parameterset_id)
    if authz.has_write_permissions(request, parameterset.experiment.id):
        return edit_parameters(request, parameterset, otype="experiment")
    else:
        return return_response_error(request)


@login_required
def edit_dataset_par(request, parameterset_id):
    parameterset = DatasetParameterSet.objects.get(id=parameterset_id)
    if authz.has_write_permissions(request,
                                   parameterset.dataset.experiment.id):
        return edit_parameters(request, parameterset, otype="dataset")
    else:
        return return_response_error(request)


@login_required
def edit_datafile_par(request, parameterset_id):
    parameterset = DatafileParameterSet.objects.get(id=parameterset_id)
    if authz.has_write_permissions(request,
                            parameterset.dataset_file.dataset.experiment.id):
        return edit_parameters(request, parameterset, otype="datafile")
    else:
        return return_response_error(request)


def edit_parameters(request, parameterset, otype):

    parameternames = ParameterName.objects.filter(
        schema__namespace=parameterset.schema.namespace)
    success = False
    valid = True

    if request.method == 'POST':

        class DynamicForm(create_parameterset_edit_form(
            parameterset, request=request)):
            pass

        form = DynamicForm(request.POST)

        if form.is_valid():
            save_datafile_edit_form(parameterset, request)

            success = True
        else:
            valid = False

    else:

        class DynamicForm(create_parameterset_edit_form(
            parameterset)):
            pass

        form = DynamicForm()

    c = Context({
        'schema': parameterset.schema,
        'form': form,
        'parameternames': parameternames,
        'type': otype,
        'success': success,
        'parameterset_id': parameterset.id,
        'valid': valid,
    })

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/parameteredit.html', c))


@login_required
def add_datafile_par(request, datafile_id):
    parentObject = Dataset_File.objects.get(id=datafile_id)
    if authz.has_write_permissions(request,
                                   parentObject.dataset.experiment.id):
        return add_par(request, parentObject, otype="datafile",
                stype=Schema.DATAFILE)
    else:
        return return_response_error(request)


@login_required
def add_dataset_par(request, dataset_id):
    parentObject = Dataset.objects.get(id=dataset_id)
    if authz.has_write_permissions(request, parentObject.experiment.id):
        return add_par(request, parentObject, otype="dataset",
                stype=Schema.DATASET)
    else:
        return return_response_error(request)


@login_required
def add_experiment_par(request, experiment_id):
    parentObject = Experiment.objects.get(id=experiment_id)
    if authz.has_write_permissions(request, parentObject.id):
        return add_par(request, parentObject, otype="experiment",
                stype=Schema.EXPERIMENT)
    else:
        return return_response_error(request)


def add_par(request, parentObject, otype, stype):

    all_schema = Schema.objects.filter(type=stype, immutable=False)

    if 'schema_id' in request.GET:
        schema_id = request.GET['schema_id']
    else:
        schema_id = all_schema[0].id

    schema = Schema.objects.get(id=schema_id)

    parameternames = ParameterName.objects.filter(
        schema__namespace=schema.namespace)

    success = False
    valid = True

    if request.method == 'POST':

        class DynamicForm(create_datafile_add_form(
            schema.namespace, parentObject, request=request)):
            pass

        form = DynamicForm(request.POST)

        if form.is_valid():
            save_datafile_add_form(schema.namespace, parentObject, request)

            success = True
        else:
            valid = False

    else:

        class DynamicForm(create_datafile_add_form(
            schema.namespace, parentObject)):
            pass

        form = DynamicForm()

    c = Context({
        'schema': schema,
        'form': form,
        'parameternames': parameternames,
        'type': otype,
        'success': success,
        'valid': valid,
        'parentObject': parentObject,
        'all_schema': all_schema,
        'schema_id': schema.id,
    })

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/parameteradd.html', c))

class ExperimentSearchView(SearchView):
    def __name__(self):
        return "ExperimentSearchView"

    def extra_context(self):
        extra = super(ExperimentSearchView, self).extra_context()
        # Results may contain Experiments, Datasets and Dataset_Files.
        # Group them into experiments, noting whether or not the search
        # hits were in the Dataset(s) or Dataset_File(s)
        results = self.results
        facets =  results.facet_counts()
        if facets:
            experiment_facets = facets['fields']['experiment_id_stored']
            experiment_ids = [ int(f[0]) for f in experiment_facets if int(f[1]) > 0 ]
        else:
            experiment_ids = []


        access_list = []

        if self.request.user.is_authenticated():
            access_list.extend([e.pk for e in authz.get_accessible_experiments(self.request)])

        access_list.extend([e.pk for e in Experiment.objects.filter(public=True)])

        ids = list(set(experiment_ids) & set(access_list))
        experiments = Experiment.objects.filter(pk__in=ids).order_by('-update_time')

        results = []
        for e in experiments:
            result = {}
            result['sr'] = e
            result['dataset_hit'] = False
            result['dataset_file_hit'] = False
            result['experiment_hit'] = False
            results.append(result)

        extra['experiments'] = results
        return extra

    # override SearchView's method in order to
    # return a ResponseContext
    def create_response(self):
        (paginator, page) = self.build_page()

        # Remove unnecessary whitespace
        # TODO this should just be done in the form clean...
        query = SearchQueryString(self.query)
        context = {
                'search_query': query,
                'form': self.form,
                'page': page,
                'paginator' : paginator,
                }
        context.update(self.extra_context())

        return render_response_index(self.request, self.template, context)


@login_required
def single_search(request):
    search_query = FacetFixedSearchQuery(backend=HighlightSearchBackend())
    sqs = SearchQuerySet(query=search_query)
    sqs.highlight()

    return ExperimentSearchView(
            template = 'search/search.html',
            searchqueryset=sqs,
            form_class=RawSearchForm,
            ).__call__(request)


@never_cache
@authz.experiment_ownership_required
def publish_experiment(request, experiment_id):
    """
    Make the experiment open to public access.
    Sets off a chain of PublishProvider modules for
    extra publish functionality.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be published
    :type experiment_id: string

    """
    import os

    experiment = Experiment.objects.get(id=experiment_id)
    username = request.user.username

    if request.method == 'POST':  # If the form has been submitted...

        legal = True
        success = True
        messages = []

        context_dict = {}
        context_dict['publish_result'] = "submitted"

        passed_ands = False

        opt_out_ands = False
        if 'ands_register' in request.POST:
            opt_out_ands = True

        has_ands_registered = True
        if 'monash_ands' in getTardisApps():
            from tardis.apps.monash_ands.MonashANDSService\
                import MonashANDSService

            monashandsService = MonashANDSService(experiment_id)

            has_ands_registered = monashandsService.has_registration_record()

        passed_ands = has_ands_registered or opt_out_ands

        if passed_ands == False:
            success = False
            messages.append('You must opt out of ANDS registration, or' + \
                ' register with ANDS')

        if  not 'legal' in request.POST:
            logger.debug('Legal agreement for exp: ' + experiment_id +
            ' not accepted.')
            legal = False

        if legal and success:
            experiment.public = True
            experiment.save()

        # set dictionary to legal status and publish success result
        context_dict['legal'] = legal
        context_dict['success'] = success
        context_dict['messages'] = messages

    else:

        has_ands_registered = True

        if 'monash_ands' in getTardisApps():
            from tardis.apps.monash_ands.MonashANDSService\
                import MonashANDSService

            monashandsService = MonashANDSService(experiment_id)

            if not monashandsService.has_registration_record():
                has_ands_registered = False

        TARDIS_ROOT = os.path.abspath(\
        os.path.join(os.path.dirname(__file__)))

        legalpath = os.path.join(TARDIS_ROOT,
                      "legal.txt")

        # if legal file isn't found then we can't proceed
        try:
            legalfile = open(legalpath, 'r')
        except IOError:
            logger.error('legal.txt not found. Publication halted.')
            return return_response_error(request)

        legaltext = legalfile.read()
        legalfile.close()

        cch = CreativeCommonsHandler(experiment_id=experiment_id, create=False)

        context_dict = \
        {'username': username,
        'experiment': experiment,
        'legaltext': legaltext,
        'has_cc_license': cch.has_cc_license(),
        'has_ands_registered': has_ands_registered,
        }

    c = Context(context_dict)
    return HttpResponse(render_response_index(request,
                        'tardis_portal/publish_experiment.html', c))


@authz.experiment_ownership_required
def choose_license(request, experiment_id):
    experiment = Experiment.objects.get(id=experiment_id)
    context_dict = {'submit': False,
        'experiment': experiment}
    if request.method == 'POST':
        cch = CreativeCommonsHandler(experiment_id=experiment_id)
        cch.save_license(request)
        context_dict['submit'] = True

    c = Context(context_dict)
    return HttpResponse(render_response_index(request,
                        'tardis_portal/choose_license.html', c))


@require_POST
@authz.experiment_ownership_required
def create_token(request, experiment_id):
    experiment = Experiment.objects.get(id=experiment_id)
    token = Token(experiment=experiment, user=request.user)
    token.save_with_random_token()
    logger.info('created token: %s' % token)
    return HttpResponse('{"success": true}', mimetype='application/json');


@require_POST
def token_delete(request, token_id):
    token = Token.objects.get(id=token_id)
    if authz.has_experiment_ownership(request, token.experiment_id):
        token.delete()
        return HttpResponse('{"success": true}', mimetype='application/json');


def token_login(request, token):
    django_logout(request)

    from tardis.tardis_portal.auth import login, token_auth
    logger.debug('token login')

    user = token_auth.authenticate(request, token)
    if not user:
        return return_response_error(request)
    login(request, user)
    experiment = Experiment.objects.get(token__token=token)
    return HttpResponseRedirect(experiment.get_absolute_url())

@authz.experiment_access_required
def view_rifcs(request, experiment_id):
    """View the rif-cs of an existing experiment.

    :param request: a HTTP Request instance
    :type request: :class:`django.http.HttpRequest`
    :param experiment_id: the ID of the experiment to be viewed
    :type experiment_id: string
    :rtype: :class:`django.http.HttpResponse`

    """
    try:
        experiment = Experiment.safe.get(request, experiment_id)
    except PermissionDenied:
        return return_response_error(request)
    except Experiment.DoesNotExist:
        return return_response_not_found(request)

    try:
        rifcs_provs = settings.RIFCS_PROVIDERS
    except AttributeError:
        rifcs_provs = ()

    from tardis.tardis_portal.publish.publishservice import PublishService
    pservice = PublishService(rifcs_provs, experiment)
    context = pservice.get_context()
    if context is None:
        # return error page or something
        return return_response_error(request)

    template = pservice.get_template()
    return HttpResponse(render_response_index(request,
                        template, context), mimetype="text/xml")


