# -*- coding: UTF-8 -*-
import re
import time, datetime
import csv
import solr
import cgi
import logging

from os import path
from copy import deepcopy

# from django.http import HttpResponse, HttpResponseRedirect
#from cspace_django_site.main import cspace_django_site

# global variables

from appconfig import MAXMARKERS, MAXRESULTS, MAXLONGRESULTS, MAXFACETS, IMAGESERVER, BMAPPERSERVER, BMAPPERDIR
from appconfig import BMAPPERURL, BMAPPERCONFIGFILE, LOCALDIR, SEARCH_QUALIFIERS
from appconfig import EMAILABLEURL, SUGGESTIONS, LAYOUT, CSPACESERVER, INSTITUTION
from appconfig import VERSION, FIELDDEFINITIONS, getParms
from appconfig import DROPDOWNS, FIELDS, FACETS, LOCATION, PARMS, SEARCHCOLUMNS, SEARCHROWS, SOLRSERVER, SOLRCORE, TITLE, DEFAULTSORTKEY

SolrIsUp = True  # an initial guess! this is verified below...


def loginfo(infotype, context, request):
    logdata = ''
    #user = getattr(request, 'user', None)
    if request.user and not request.user.is_anonymous():
        username = request.user.username
    else:
        username = '-'
    if 'count' in context:
        count = context['count']
    else:
        count = '-'
    if 'querystring' in context:
        logdata = context['querystring']
    if 'url' in context:
        logdata += ' :: %s' % context['url']
    logger.info('%s :: %s :: %s :: %s' % (infotype, count, username, logdata))


def getfromXML(element, xpath):
    result = element.find(xpath)
    if result is None: return ''
    result = '' if result.text is None else result.text
    result = re.sub(r"^.*\)'(.*)'$", "\\1", result)
    return result


def deURN(urn):
    #find identifier in URN
    m = re.search("\'(.*)\'$", urn)
    if m is not None:
        # strip out single quotes
        return m.group(0)[1:len(m.group(0)) - 1]


def getfields(fieldset):
    # for solr faceting
    if fieldset == 'inCSV':
        pickField = 'name'
    elif fieldset == 'Facet':
        pickField = 'solrfield'
    elif fieldset == 'FacetLabels':
        pickField = 'label'
        fieldset = 'Facet'
    elif fieldset == "bMapper":
        pickField = 'name'
    #elif fieldset == "bmapperdata":
    #    return []
    else:
        pickField = 'solrfield'

    return [f[pickField] for f in FIELDS[fieldset]]


def getfacets(response):
    #facets = response.get('facet_counts').get('facet_fields')
    facets = response.facet_counts
    facets = facets['facet_fields']
    _facets = {}
    for key, values in facets.items():
        _v = []
        for k, v in values.items():
            _v.append((k, v))
        _facets[key] = sorted(_v, key=lambda (a, b): b, reverse=True)
    return _facets


def parseTerm(queryterm):
    queryterm = queryterm.strip(' ')
    terms = queryterm.split(' ')
    terms = ['"' + t + '"' for t in terms]
    result = ' AND '.join(terms)
    if 'AND' in result: result = '(' + result + ')'  # we only need to wrap the query if it has multiple terms
    return result


def makeMarker(location):
    if location:
        location = location.replace(' ', '')
        latitude, longitude = location.split(',')
        latitude = float(latitude)
        longitude = float(longitude)
        return "%0.2f,%0.2f" % (latitude, longitude)
    else:
        return None


def writeCsv(filehandle, items, writeheader, bmapper=False):
    if bmapper:
        fieldset = getfields('bMapper')
    else:
        fieldset = getfields('inCSV')
    print "Fieldset: %s" % fieldset
    writer = csv.writer(filehandle, delimiter='\t')
    # write the berkeley mapper header as the header for the csv file, if asked...
    if writeheader:
        writer.writerow(getfields('bMapper'))
    for item in items:
        # get the cells from the item dict in the order specified; make empty cells if key is not found.
        row = []
        if bmapper:
            r = []
            for x in item['otherfields']:
                if x['name'] not in fieldset:
                    continue
                r.append(x['value'])
            location = item['location']
            l = location.split(',')
            r.append(l[0])
            r.append(l[1])
            for cell in r:
                # the following few lines are a hack to handle non-unicode data which appears to be present in the solr datasource
                if isinstance(cell, unicode):
                    try:
                        cell = cell.translate({0xd7: u"x"})
                        cell = cell.decode('utf-8', 'ignore').encode('utf-8')
                    except:
                        print 'unicode problem', cell.encode('utf-8', 'ignore')
                        cell = u'invalid unicode data'
                row.append(cell)
            writer.writerow(row)
            continue
        for x in item['otherfields']:
            if x['name'] not in fieldset:
                continue
            cell = x['value']
            # the following few lines are a hack to handle non-unicode data which appears to be present in the solr datasource
            if isinstance(cell, unicode):
                try:
                    cell = cell.translate({0xd7: u"x"})
                    cell = cell.decode('utf-8', 'ignore').encode('utf-8')
                except:
                    print 'unicode problem', cell.encode('utf-8', 'ignore')
                    cell = u'invalid unicode data'
            row.append(cell)
        writer.writerow(row)


def getMapPoints(context, requestObject):
    mappableitems = []
    if 'select-item' in requestObject:
        mapitems = context['items']
        numSelected = len(mapitems)
    else:
        selected = []
        for p in requestObject:
            if 'item-' in p:
                selected.append(requestObject[p])
        numSelected = len(selected)
        mapitems = []
        for item in context['items']:
            if item['csid'] in selected:
                mapitems.append(item)
    for item in mapitems:
        try:
            m = makeMarker(item['location'])
            if m is not None:
                mappableitems.append(item)
        except KeyError:
            pass
    return mappableitems, numSelected


def setupGoogleMap(requestObject, context):
    context = doSearch(context)
    selected = []
    for p in requestObject:
        if 'item-' in p:
            selected.append(requestObject[p])
    mappableitems = []
    markerlist = []
    markerlength = 200
    for item in context['items']:
        if item['csid'] in selected:
            #if True:
            try:
                m = makeMarker(item['location'])
                if markerlength > 2048: break
                #if len(mappableitems) >= MAXMARKERS: break
                if m is not None:
                    #print 'm= x%sx' % m
                    markerlist.append(m)
                    mappableitems.append(item)
                    markerlength += len(m) + 8  # 8 is the length of '&markers='
            except KeyError:
                pass
    context['mapmsg'] = []
    if len(context['items']) < context['count']:
        context['mapmsg'].append('%s points plotted. %s selected objects examined (of %s in result set).' % (
            len(markerlist), len(selected), context['count']))
    else:
        context['mapmsg'].append(
            '%s points plotted. all %s selected objects in result set examined.' % (len(markerlist), len(selected)))
    context['items'] = mappableitems
    context['markerlist'] = '&markers='.join(markerlist)
    #context['markerlist'] = '&markers='.join(markerlist[:MAXMARKERS])

    #if len(markerlist) >= MAXMARKERS:
    #    context['mapmsg'].append(
    #        '%s points is the limit. Only first %s accessions (with latlongs) plotted!' % (MAXMARKERS, len(markerlist)))

    return context


def setupBMapper(requestObject, context):
    context['berkeleymapper'] = 'set'
    context = doSearch(context)
    mappableitems, numSelected = getMapPoints(context, requestObject)
    context['mapmsg'] = []
    filename = 'bmapper%s.csv' % datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    #filehandle = open(filename, 'wb')
    filehandle = open(path.join(LOCALDIR, filename), 'wb')
    writeCsv(filehandle, mappableitems, writeheader=False, bmapper=True)
    filehandle.close()
    context['mapmsg'].append('%s points of the %s selected objects examined had latlongs (%s in result set).' % (
        len(mappableitems), numSelected, context['count']))
    #context['mapmsg'].append('if our connection to berkeley mapper were working, you be able see them plotted there.')
    context['items'] = mappableitems
    bmapperconfigfile = '%s/%s/%s' % (BMAPPERSERVER, BMAPPERDIR, BMAPPERCONFIGFILE)
    tabfile = '%s/%s/%s' % (BMAPPERSERVER, BMAPPERDIR, filename)
    context['bmapperurl'] = BMAPPERURL % (tabfile, bmapperconfigfile)
    return context
    # return HttpResponseRedirect(context['bmapperurl'])


def setupCSV(requestObject, context):
    context = doSearch(context)
    selected = []
    # check to see if 'select all' is clicked...if so, skip checking individual items
    if 'select-item' in requestObject:
        csvitems = context['items']
    else:
        for p in requestObject:
            if 'item-' in p:
                selected.append(requestObject[p])
        csvitems = []
        for item in context['items']:
            if item['csid'] in selected:
                csvitems.append(item)
    return csvitems


def setDisplayType(requestObject):
    if 'displayType' in requestObject:
        displayType = requestObject['displayType']
    elif 'search-list' in requestObject:
        displayType = 'list'
    elif 'search-full' in requestObject:
        displayType = 'full'
    elif 'search-grid' in requestObject:
        displayType = 'grid'
    else:
        displayType = 'list'

    return displayType


def extractValue(listItem, key):
    # make all arrays into strings for display
    if key in listItem:
        if type(listItem[key]) == type([]):
            temp = ', '.join(listItem[key])
        else:
            temp = listItem[key]
    else:
        temp = ''

    # handle dates (convert them to collatable strings)
    if isinstance(temp, datetime.date):
        try:
            #item[p] = item[p].toordinal()
            temp = temp.isoformat().replace('T00:00:00+00:00', '')
        except:
            print 'date problem: ', temp

    return temp


def setConstants(context):
    if not SolrIsUp: context['errormsg'] = 'Solr is down!'
    context['suggestsource'] = SUGGESTIONS
    context['title'] = TITLE
    context['apptitle'] = TITLE
    context['imageserver'] = IMAGESERVER
    context['cspaceserver'] = CSPACESERVER
    context['institution'] = INSTITUTION
    context['emailableurl'] = EMAILABLEURL
    context['version'] = VERSION
    context['layout'] = LAYOUT
    context['dropdowns'] = FACETS
    context['timestamp'] = time.strftime("%b %d %Y %H:%M:%S", time.localtime())
    context['qualifiers'] = [{'val': s, 'dis': s} for s in SEARCH_QUALIFIERS]
    context['resultoptions'] = [100, 500, 1000, 2000, 10000]

    context['searchrows'] = range(SEARCHROWS + 1)[1:]
    context['searchcolumns'] = range(SEARCHCOLUMNS + 1)[1:]

    emptyCells = {}
    for row in context['searchrows']:
        for col in context['searchcolumns']:
            empty = True
            for field in FIELDS['Search']:
                if field['row'] == row and field['column'] == col:
                    empty = False
            if empty:
                if not row in emptyCells:
                    emptyCells[row] = {}
                emptyCells[row][col] = 'X'
    context['emptycells'] = emptyCells

    context['displayTypes'] = (
        ('list', 'List'),
        ('full', 'Full'),
        ('grid', 'Grid'),
    )

    # copy over form values to context if they exist
    try:
        requestObject = context['searchValues']

        # build a list of the search term qualifiers used in this query (for templating...)
        qualfiersInUse = []
        for formkey, formvalue in requestObject.items():
            if '_qualifier' in formkey:
                qualfiersInUse.append(formkey + ':' + formvalue)

        context['qualfiersInUse'] = qualfiersInUse

        context['displayType'] = setDisplayType(requestObject)
        if 'url' in requestObject: context['url'] = requestObject['url']
        if 'querystring' in requestObject: context['querystring'] = requestObject['querystring']
        if 'core' in requestObject: context['core'] = requestObject['core']
        if 'pixonly' in requestObject: context['pixonly'] = requestObject['pixonly']
        if 'maxresults' in requestObject: context['maxresults'] = int(requestObject['maxresults'])
        context['start'] = int(requestObject['start']) if 'start' in requestObject else 1
        context['maxfacets'] = int(requestObject['maxfacets']) if 'maxfacets' in requestObject else MAXFACETS
        context['sortkey'] = requestObject['sortkey'] if 'sortkey' in requestObject else DEFAULTSORTKEY
    except:
        print "no searchValues set"
        context['displayType'] = setDisplayType({})
        context['url'] = ''
        context['querystring'] = ''
        context['core'] = SOLRCORE
        context['maxresults'] = 0
        context['start'] = 1
        context['sortkey'] = DEFAULTSORTKEY

    if context['start'] < 1: context['start'] = 1

    context['PARMS'] = PARMS
    if not 'FIELDS' in context:
        context['FIELDS'] = FIELDS

    return context


def doSearch(context):
    solr_server = SOLRSERVER
    solr_core = SOLRCORE
    elapsedtime = time.time()
    context = setConstants(context)
    requestObject = context['searchValues']

    formFields = deepcopy(FIELDS)
    for searchfield in formFields['Search']:
        if searchfield['name'] in requestObject.keys():
            searchfield['value'] = requestObject[searchfield['name']]
        else:
            searchfield['value'] = ''

    context['FIELDS'] = formFields

    # create a connection to a solr server
    s = solr.SolrConnection(url='%s/%s' % (solr_server, solr_core))
    queryterms = []
    urlterms = []
    #facetfields = getfields(context['facets'])
    facetfields = getfields('Facet')
    if 'map-google' in requestObject or 'csv' in requestObject or 'map-bmapper' in requestObject:
        querystring = requestObject['querystring']
        url = requestObject['url']
        # Did the user request the full set?
        if 'select-item' in requestObject:
            context['maxresults'] = min(requestObject['count'], MAXRESULTS)
            context['start'] = 1
    else:
        for p in requestObject:
            if p in ['csrfmiddlewaretoken', 'displayType', 'resultsOnly', 'maxresults', 'url', 'querystring', 'pane',
                     'pixonly', 'locsonly', 'acceptterms', 'submit', 'start', 'sortkey']: continue
            if '_qualifier' in p: continue
            if 'select-' in p: continue  # skip select control for map markers
            if not requestObject[p]: continue  # uh...looks like we can have empty items...let's skip 'em
            if 'item-' in p: continue
            searchTerm = requestObject[p]
            terms = searchTerm.split(' OR ')
            ORs = []
            querypattern = '%s:%s'  # default search expression pattern (dates are different)
            for t in terms:
                t = t.strip()
                if t == 'Null':
                    t = '[* TO *]'
                    index = '-' + PARMS[p][3]
                else:
                    if p in DROPDOWNS:
                        # if it's a value in a dropdown, it must always be an "exact search"
                        t = '"' + t + '"'
                        index = PARMS[p][3].replace('_txt', '_s')
                    elif p + '_qualifier' in requestObject:
                        # print 'qualifier:',requestObject[p+'_qualifier']
                        qualifier = requestObject[p + '_qualifier']
                        if qualifier == 'exact':
                            index = PARMS[p][3].replace('_txt', '_s')
                            t = '"' + t + '"'
                        elif qualifier == 'phrase':
                            index = PARMS[p][3].replace('_ss', '_txt')
                            index = index.replace('_s', '_txt')
                            t = '"' + t + '"'
                        elif qualifier == 'keyword':
                            t = t.split(' ')
                            t = ' +'.join(t)
                            t = '(+' + t + ')'
                            t = t.replace('+-', '-')  # remove the plus if user entered a minus
                            index = PARMS[p][3].replace('_ss', '_txt')
                            index = index.replace('_s', '_txt')
                    elif '_dt' in PARMS[p][3]:
                        querypattern = '%s: "%sZ"'
                        index = PARMS[p][3]
                    else:
                        t = t.split(' ')
                        t = ' +'.join(t)
                        t = '(+' + t + ')'
                        t = t.replace('+-', '-')  # remove the plus if user entered a minus
                        index = PARMS[p][3]
                if t == 'OR': t = '"OR"'
                if t == 'AND': t = '"AND"'
                ORs.append(querypattern % (index, t))
            searchTerm = ' OR '.join(ORs)
            if ' ' in searchTerm and not '[* TO *]' in searchTerm: searchTerm = ' (' + searchTerm + ') '
            queryterms.append(searchTerm)
            urlterms.append('%s=%s' % (p, cgi.escape(requestObject[p])))
            if p + '_qualifier' in requestObject:
                # print 'qualifier:',requestObject[p+'_qualifier']
                urlterms.append('%s=%s' % (p + '_qualifier', cgi.escape(requestObject[p + '_qualifier'])))
        querystring = ' AND '.join(queryterms)

        if urlterms != []:
            urlterms.append('displayType=%s' % context['displayType'])
            urlterms.append('maxresults=%s' % context['maxresults'])
            urlterms.append('start=%s' % context['start'])
        url = '&'.join(urlterms)

    if 'pixonly' in context:
        pixonly = context['pixonly']
        querystring += " AND %s:[* TO *]" % PARMS['blobs'][3]
        url += '&pixonly=True'
    else:
        pixonly = None

    if 'locsonly' in requestObject:
        locsonly = requestObject['locsonly']
        querystring += " AND %s:[-90,-180 TO 90,180]" % LOCATION
        url += '&locsonly=True'
    else:
        locsonly = None

    print 'Solr query: %s' % querystring
    try:
        startpage = context['maxresults'] * (context['start'] - 1)
    except:
        startpage = 0
        context['start'] = 1
    try:
        response = s.query(querystring, facet='true', facet_field=facetfields, fq={},
                           rows=context['maxresults'], facet_limit=MAXFACETS, sort=context['sortkey'],
                           facet_mincount=1, start=startpage)
        print 'Solr search succeeded, %s results, %s rows requested starting at %s' % (
        response.numFound, context['maxresults'], startpage)
    #except:
    except Exception as inst:
        #raise
        print 'Solr search failed: %s' % str(inst)
        context['errormsg'] = 'Solr4 query failed'
        return context

    results = response.results

    context['items'] = []
    imageCount = 0
    if 'berkeleymapper' in context:
        displayFields = 'bMapper'
    else:
        displayFields = context['displayType'] + 'Display'
    for i, listItem in enumerate(results):
        item = {}
        item['counter'] = i
        otherfields = []

        # pull out the fields that have special functions in the UI
        for p in PARMS:
            if 'mainentry' in PARMS[p][1]:
                item['mainentry'] = extractValue(listItem, PARMS[p][3])
            elif 'accession' in PARMS[p][1]:
                x = PARMS[p]
                item['accession'] = extractValue(listItem, PARMS[p][3])
                item['accessionfield'] = PARMS[p][4]
            if 'sortkey' in PARMS[p][1]:
                item['sortkey'] = extractValue(listItem, PARMS[p][3])

        for p in FIELDS[displayFields]:
            try:
                otherfields.append(
                    {'label': p['label'], 'name': p['name'], 'value': extractValue(listItem, p['solrfield'])})
            except:
                pass
                #raise
                #otherfields.append({'label':p['label'],'value': ''})
        item['otherfields'] = otherfields
        if 'csid_s' in listItem.keys():
            item['csid'] = listItem['csid_s']
        # the list of blob csids need to remain an array, so restore it from psql result
        if 'blob_ss' in listItem.keys():
            item['blobs'] = listItem['blob_ss']
            imageCount += len(item['blobs'])
        if LOCATION in listItem.keys():
            item['marker'] = makeMarker(listItem[LOCATION])
            item['location'] = listItem[LOCATION]
        context['items'].append(item)

    #if context['displayType'] in ['full', 'grid'] and response._numFound > MAXRESULTS:
    #    context['recordlimit'] = '(limited to %s for long display)' % MAXRESULTS
    #    context['items'] = context['items'][:MAXLONGRESULTS]
    if context['displayType'] in ['full', 'grid', 'list'] and response._numFound > context['maxresults']:
        context['recordlimit'] = '(display limited to %s)' % context['maxresults']

    # I think this hack works for most values... but really it should be done properly someday... ;-)
    numberOfPages = 1 + int(response._numFound / (context['maxresults'] + 0.001))
    context['lastpage'] = numberOfPages
    context['pagesummary'] = 'Page %s of %s [items %s to %s]. ' % (
        context['start'], numberOfPages, startpage + 1,
        min(context['start'] * context['maxresults'], response._numFound))

    context['count'] = response._numFound

    m = {}
    for p in PARMS:
        m[PARMS[p][3]] = PARMS[p][4]

    facets = getfacets(response)
    context['labels'] = [p['label'] for p in FIELDS[displayFields]]
    context['facets'] = [[m[f], facets[f]] for f in facetfields]
    context['fields'] = getfields('FacetLabels')
    context['range'] = range(len(facetfields))
    context['pixonly'] = pixonly
    context['locsonly'] = locsonly
    try:
        context['pane'] = requestObject['pane']
    except:
        context['pane'] = '0'
    try:
        context['resultsOnly'] = requestObject['resultsOnly']
    except:
        pass

    context['imagecount'] = imageCount
    context['url'] = url
    context['querystring'] = querystring
    context['core'] = solr_core
    context['time'] = '%8.3f' % (time.time() - elapsedtime)
    return context


# Get an instance of a logger, log some startup info
logger = logging.getLogger(__name__)
logger.info('%s :: %s :: %s' % ('portal startup', '-', '%s | %s | %s' % (SOLRSERVER, IMAGESERVER, BMAPPERSERVER)))