__author__ = 'jblowe, amywieliczka'

import time, datetime
from os import path

from django.contrib.auth.decorators import login_required
from cspace_django_site.settings import STATIC_URL
from cspace_django_site.settings import MEDIA_URL
from django.shortcuts import render, render_to_response
from django.http import HttpResponse, HttpResponseRedirect
from django import forms
from cspace_django_site.main import cspace_django_site
from utils import writeCsv, doSearch, setupGoogleMap, setupBMapper, setDisplayType, setConstants

# global variables (at least to this module...)
config = cspace_django_site.getConfig()

SOLRSERVER = 'http://localhost:8983/solr'
SOLRCORE = 'ucjeps-metadata'


@login_required()
def publicsearch(request):
    if request.method == 'GET':
        requestObject = request.GET
    elif request.method == 'POST':
        requestObject = request.POST
    else:
        pass
        #error!

    if requestObject == {}:
        context = {}
    else:
        form = forms.Form(requestObject)

        if form.is_valid() or request.method == 'GET':
            context = {'searchValues': requestObject}
            if 'reset' in requestObject:
                context = {}
            else:
                context = doSearch(SOLRSERVER, SOLRCORE, context)

            if 'csv' in requestObject:
                # Create the HttpResponse object with the appropriate CSV header.
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = 'attachment; filename="ucjeps.csv"'
                #response.write(u'\ufeff'.encode('utf8'))
                writeCsv(response, context['items'], writeheader=True)
                return response
            elif 'map-bmapper' in requestObject:
                context = setupBMapper(requestObject, context)
            elif 'map-google' in requestObject:
                context = setupGoogleMap(requestObject, context)
            elif 'email' in requestObject:
                pass

    context = setConstants(context)

    return render(request, 'publicsearch.html', context)
