#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# Copyright (c) 2010, Monash e-Research Centre
#   (Monash University, Australia)
# Copyright (c) 2010, VeRSI Consortium
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

from __future__ import with_statement  # This isn't required in Python 2.6
from xml.dom.minidom import parse, parseString
from tardis.tardis_portal.models import *
from tardis.tardis_portal.ExperimentParser import ExperimentParser
from django.utils.safestring import SafeUnicode
import datetime
import urllib

# todo move me out of here

from StringIO import StringIO
from lxml import etree


def getText(nodelist):
    rc = ''
    for node in nodelist:
        if node.nodeType == node.TEXT_NODE:
            rc = rc + node.data
    return rc


def getSingleResult(elements):
    if len(elements) == 1:
        return SafeUnicode(elements[0])
    else:
        return None


def getParameterFromTechXML(tech_xml, parameter_name):
    prefix = tech_xml.getroot().prefix
    xmlns = tech_xml.getroot().nsmap[prefix]

    parameter_string = ''
    for parameter in parameter_name.split('/'):
        parameter_string = parameter_string + '/' + prefix + ':' \
            + parameter

    elements = tech_xml.xpath('/' + parameter_string + '/text()',
                              namespaces={prefix: xmlns})

    print elements
    return getSingleResult(elements)


def getTechXMLFromRaw(md):
    return etree.parse(StringIO(md))


def getXmlnsFromTechXMLRaw(md):
    tech_xml = etree.parse(StringIO(md))
    prefix = tech_xml.getroot().prefix
    xmlns = tech_xml.getroot().nsmap[prefix]

    return xmlns


class ProcessExperiment:

    def download_xml(self, url):
        f = urllib.urlopen(url)
        xmlString = f.read()

        return xmlString

    def null_check(self, string):
        if string == 'null':
            return None
        else:
            return string

    def register_experiment_xmldata(self, xmldata, created_by):

        xmlString = xmldata
        url = 'http://www.example.com'
        self.url = 'http://www.example.com'

        ep = ExperimentParser(str(xmlString))

        e = Experiment(
            url=url,
            approved=True,
            title=ep.getTitle(),
            institution_name=ep.getAgentName('DISSEMINATOR'),
            description=ep.getAbstract(),
            created_by=created_by,
            )

        e.save()

        self.process_METS(e, ep)

        return e.id

    def register_experiment_xmldata_file(
        self,
        filename,
        created_by,
        expid=None,
        ):

        f = open(filename)

        firstline = f.readline()

        f.close()

        if firstline.startswith('<experiment'):
            print 'processing simple xml'
            eid = self.process_simple(filename, created_by, expid)
        else:
            print 'processing METS'
            eid = self.process_METS(filename, created_by, expid)

        return eid

    def process_METS(
        self,
        filename,
        created_by,
        expid=None,
        ):

        print 'START EXP: ' + str(expid)

        url = 'http://www.example.com'
        self.url = 'http://www.example.com'

        f = open(filename, 'r')
        xmlString = f.read()
        f.close()

        ep = ExperimentParser(str(xmlString))

        del xmlString

        e = Experiment(
            id=expid,
            url=url,
            approved=True,
            title=ep.getTitle(),
            institution_name=ep.getAgentName('DISSEMINATOR'),
            description=ep.getAbstract(),
            created_by=created_by,
            )

        e.save()

        url_path = self.url.rpartition('/')[0] + self.url.rpartition('/'
                )[1]

        author_experiments = Author_Experiment.objects.all()
        author_experiments = \
            author_experiments.filter(experiment=e).delete()

        x = 0
        for authorName in ep.getAuthors():
            author = Author(name=SafeUnicode(authorName))
            author.save()
            author_experiment = Author_Experiment(experiment=e,
                    author=author, order=x)
            author_experiment.save()
            x = x + 1

        e.dataset_set.all().delete()

        for dmdid in ep.getDatasetDMDIDs():
            d = Dataset(experiment=e,
                        description=ep.getDatasetTitle(dmdid))
            d.save()
            for admid in ep.getDatasetADMIDs(dmdid):

                techxml = ep.getTechXML(admid)
                prefix = techxml.getroot().prefix
                xmlns = techxml.getroot().nsmap[prefix]

                try:

                    schema = Schema.objects.get(namespace__exact=xmlns)

                    parameternames = \
                        ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                    parameternames = parameternames.order_by('id')

                    for pn in parameternames:

                        if pn.is_numeric:
                            value = ep.getParameterFromTechXML(techxml,
                                    pn.name)

                            if value != None:
                                dp = DatasetParameter(dataset=d,
                                        name=pn, string_value=None,
                                        numerical_value=float(value))
                                dp.save()
                        else:
                            dp = DatasetParameter(dataset=d, name=pn,
                                    string_value=ep.getParameterFromTechXML(techxml,
                                    pn.name), numerical_value=None)
                            dp.save()
                except Schema.DoesNotExist:

                    print 'Schema ' + xmlns + " doesn't exist!"

                        # todo replace with logging

            for fileid in ep.getFileIDs(dmdid):

                # if ep.getFileLocation(fileid).startswith('file://'):
                # ....................absolute_filename = url_path + ep.getFileLocation(fileid).partition('//')[2]
                # ................else:
                # ....................absolute_filename = ep.getFileLocation(fileid)....

                if self.null_check(ep.getFileName(fileid)):
                    filename = ep.getFileName(fileid)
                else:
                    filename = ep.getFileLocation(fileid).rpartition('/'
                            )[2]

                # print filename

                datafile = Dataset_File(dataset=d, filename=filename,
                        url=ep.getFileLocation(fileid),
                        size=ep.getFileSize(fileid))
                datafile.save()

                for admid in ep.getFileADMIDs(fileid):

                    techxml = ep.getTechXML(admid)
                    prefix = techxml.getroot().prefix
                    xmlns = techxml.getroot().nsmap[prefix]

                    try:
                        schema = \
                            Schema.objects.get(namespace__exact=xmlns)

                        parameternames = \
                            ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                        parameternames = parameternames.order_by('id')

                        for pn in parameternames:

                            if pn.is_numeric:
                                value = \
                                    ep.getParameterFromTechXML(techxml,
                                        pn.name)
                                if value != None:
                                    dp = \
    DatafileParameter(dataset_file=datafile, name=pn,
                      string_value=None, numerical_value=float(value))
                                    dp.save()
                            else:
                                dp = \
                                    DatafileParameter(dataset_file=datafile,
                                        name=pn,
                                        string_value=ep.getParameterFromTechXML(techxml,
                                        pn.name), numerical_value=None)
                                dp.save()
                    except Schema.DoesNotExist:

                        xml_data = XML_data(datafile=datafile,
                                xmlns=SafeUnicode(xmlns),
                                data=SafeUnicode(techxml.getvalue()))
                        xml_data.save()

        print 'DONE EXP: ' + str(e.id)

        return e.id

    # this is the worst code of all time :) -steve

    def process_simple(
    self,
    filename,
    created_by,
    eid,
    ):

        url = 'http://www.example.com'
        self.url = 'http://www.example.com'

        with open(filename) as f:
            e = 0
            ds = 0
            df = 0
            current = None
            current_df_id = 0
            mdelist = []
            for line in f:
                line = line.strip()

            # print "LINE: " + line

                if line.startswith('<experiment>'):
                    current = 'experiment'
                    e = e + 1
                    ds = 0
                    df = 0
                    print 'experiment: ' + str(e)

                    exp = dict()
                    authors = list()
                elif line.startswith('<dataset>'):

                # commit any experiment if current = experiment

                    if current == 'experiment':

                        experiment = Experiment(
                            id=eid,
                            url=url,
                            approved=True,
                            title=exp['title'],
                            institution_name=exp['organization'],
                            description=exp['abstract'],
                            created_by=created_by,
                            )

                        experiment.save()

                        author_experiments = \
                            Author_Experiment.objects.all()
                        author_experiments = \
                            author_experiments.filter(experiment=experiment).delete()

                        x = 0
                        for authorName in authors:
                            author = \
                                Author(name=SafeUnicode(authorName))
                            author.save()
                            author_experiment = \
                                Author_Experiment(experiment=experiment,
                                    author=author, order=x)
                            author_experiment.save()
                            x = x + 1

                        experiment.dataset_set.all().delete()

                        if exp.has_key('metadata'):
                            for md in exp['metadata']:
                                xmlns = getXmlnsFromTechXMLRaw(md)

                                try:
                                    print 'trying to find parameters with an xmlns of ' \
        + xmlns
                                    schema = \
        Schema.objects.get(namespace__exact=xmlns)

                                    parameternames = \
        ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                                    parameternames = \
        parameternames.order_by('id')

                                    tech_xml = getTechXMLFromRaw(md)

                                    parameterset = \
        ExperimentParameterSet(schema=schema, experiment=experiment)

                                    parameterset.save()

                                    for pn in parameternames:
                                        try:

                                        # print "finding parameter " + pn.name + " in metadata"

                                            if pn.is_numeric:
                                                value = \
        getParameterFromTechXML(tech_xml, pn.name)
                                                if value != None:
                                                    ep = \
        ExperimentParameter(parameterset=parameterset, name=pn,
                    string_value=None, numerical_value=float(value))
                                                    ep.save()
                                            else:
                                                ep = \
        ExperimentParameter(parameterset=parameterset, name=pn,
                    string_value=getParameterFromTechXML(tech_xml,
                    pn.name), numerical_value=None)
                                                ep.save()
                                        except e:
                                            print e
                                except Schema.DoesNotExist, e:
                                    print e

                    current = 'dataset'
                    ds = ds + 1
                    mdflist = []
                    mdslist = []
                    df = 0
                    dataset = dict()
                    print 'experiment: ' + str(e) + ' dataset: ' \
                        + str(ds)
                elif line.startswith('<file>'):

                    if current == 'dataset':
                        d = Dataset(experiment=experiment,
                                description=dataset['description'])
                        d.save()
                        print dataset

                        if dataset.has_key('metadata'):
                            for md in dataset['metadata']:
                                if dataset.has_key('metadata'):
                                    xmlns = \
        getXmlnsFromTechXMLRaw(md)

                                    try:
                                        print 'trying to find parameters with an xmlns of ' \
        + xmlns
                                        schema = \
        Schema.objects.get(namespace__exact=xmlns)

                                        parameternames = \
        ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                                        parameternames = \
        parameternames.order_by('id')

                                        tech_xml = \
        getTechXMLFromRaw(md)

                                        parameterset = \
        DatasetParameterSet(schema=schema, dataset=d)

                                        parameterset.save()

                                        for pn in parameternames:
                                            try:

                                            # print "finding parameter " + pn.name + " in metadata"

                                                if pn.is_numeric:
                                                    value = \
        getParameterFromTechXML(tech_xml, pn.name)
                                                    if value \
        != None:
                                                        dp = \
        DatasetParameter(parameterset=parameterset, name=pn,
                 string_value=None, numerical_value=float(value))
                                                        dp.save()
                                                else:
                                                    dp = \
        DatasetParameter(parameterset=parameterset, name=pn,
                 string_value=getParameterFromTechXML(tech_xml,
                 pn.name), numerical_value=None)
                                                    dp.save()
                                            except e:
                                                print e
                                    except Schema.DoesNotExist, e:
                                        print e
                    else:
                        if self.null_check(datafile['name']):
                            filename = datafile['name']
                        else:
                            filename = datafile['path']

                    # print filename

                        dfile = Dataset_File(dataset=d,
                                filename=filename,
                                url=datafile['path'],
                                size=datafile['size'])
                        dfile.save()
                        current_df_id = dfile.id

                        print datafile

                        for md in datafile['metadata']:
                            xmlns = getXmlnsFromTechXMLRaw(md)

                            try:
                                print 'trying to find parameters with an xmlns of ' \
        + xmlns
                                schema = \
        Schema.objects.get(namespace__exact=xmlns)

                                parameternames = \
        ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                                parameternames = \
        parameternames.order_by('id')

                                tech_xml = getTechXMLFromRaw(md)

                                parameterset = \
        DatafileParameterSet(schema=schema, dataset_file=dfile)

                                parameterset.save()

                                for pn in parameternames:
                                    try:

                                    # print "finding parameter " + pn.name + " in metadata"

                                        dfile = \
        Dataset_File.objects.get(pk=current_df_id)
                                        if pn.is_numeric:
                                            value = \
        getParameterFromTechXML(tech_xml, pn.name)
                                            if value != None:
                                                dp = \
        DatafileParameter(parameterset=parameterset, name=pn,
                  string_value=None, numerical_value=float(value))
                                                dp.save()
                                        else:
                                            dp = \
        DatafileParameter(parameterset=parameterset, name=pn,
                  string_value=getParameterFromTechXML(tech_xml,
                  pn.name), numerical_value=None)
                                            dp.save()
                                    except e:
                                        print e
                            except Schema.DoesNotExist, e:
                                print e

                # commit any dataset if current = dataset

                    current = 'file'
                    df = df + 1
                    mdflist = []
                    datafile = dict()
                    print 'experiment: ' + str(e) + ' dataset: ' \
                        + str(ds) + ' datafile: ' + str(df)
                elif line.startswith('<metadata'):

                    md = ''
                    while line.strip() != '</metadata>':
                        line = f.next()
                        if line.strip() != '</metadata>':
                            md = md + line
                    if current == 'file':
                        mdflist.append(md)
                        datafile['metadata'] = mdflist
                    elif current == 'experiment':
                        mdelist.append(md)
                        exp['metadata'] = mdelist
                    else:
                        mdslist.append(md)
                        dataset['metadata'] = mdslist
                elif line.startswith('<abstract'):

                    ab = line.partition('<abstract>')[2]
                    print 'found abstract'
                    while not line.strip().endswith('</abstract>'):
                        line = f.next()
                        ab = ab + line.partition('</abstract>')[0]
                    print 'ABSTRACTAMUNDO = ' + ab
                    exp['abstract'] = ab
                elif line.startswith('</experiment>'):

                    if current == 'dataset':
                        d = Dataset(experiment=experiment,
                                description=dataset['description'])
                        d.save()
                        print dataset
                    else:

                        if self.null_check(datafile['name']):
                            filename = datafile['name']
                        else:
                            filename = datafile['path']

                    # print filename

                        dfile = Dataset_File(dataset=d,
                                filename=filename,
                                url=datafile['path'],
                                size=datafile['size'])
                        dfile.save()

                        print dfile.id
                        current_df_id = dfile.id

                        print datafile

                        if datafile.has_key('metadata'):
                            for md in datafile['metadata']:
                                xmlns = getXmlnsFromTechXMLRaw(md)

                                try:
                                    print 'trying to find parameters with an xmlns of ' \
        + xmlns
                                    schema = \
        Schema.objects.get(namespace__exact=xmlns)

                                    parameternames = \
        ParameterName.objects.filter(schema__namespace__exact=schema.namespace)
                                    parameternames = \
        parameternames.order_by('id')

                                    tech_xml = getTechXMLFromRaw(md)

                                    parameterset = \
        DatafileParameterSet(schema=schema, dataset_file=dfile)

                                    parameterset.save()

                                    for pn in parameternames:
                                        try:

                                        # print "finding parameter " + pn.name + " in metadata"

                                            dfile = \
        Dataset_File.objects.get(pk=current_df_id)
                                            if pn.is_numeric:
                                                value = \
        getParameterFromTechXML(tech_xml, pn.name)
                                                if value != None:
                                                    dp = \
        DatafileParameter(parameterset=parameterset, name=pn,
                  string_value=None, numerical_value=float(value))
                                                    dp.save()
                                            else:
                                                dp = \
        DatafileParameter(parameterset=parameterset, name=pn,
                  string_value=getParameterFromTechXML(tech_xml,
                  pn.name), numerical_value=None)
                                                dp.save()
                                        except e:
                                            print e
                                except Schema.DoesNotExist, e:
                                    print e
                try:
                    print 'attempting to parse line: ' + line
                    dom = parseString(line)
                    doc = dom.documentElement

                # print doc.tagName + ": " + getText(contents)

                    tag_name = doc.tagName
                    print tag_name + ' discovered'
                    if current == 'experiment':
                        if tag_name == 'title' or tag_name \
                            == 'organization':
                            contents = doc.childNodes
                            exp[tag_name] = getText(contents)
                            print '\tADDED ' + tag_name + ': ' \
                                + getText(contents)
                        if tag_name == 'author':
                            contents = doc.childNodes
                            authors.append(getText(contents))
                            print '\tADDED ' + tag_name + ': ' \
                                + getText(contents)
                    if current == 'dataset':
                        if tag_name == 'description':
                            contents = doc.childNodes
                            dataset[tag_name] = getText(contents)
                            print '\t' + tag_name + ': ' \
                                + getText(contents)
                    if current == 'file':
                        if tag_name == 'name' or tag_name == 'size' \
                            or tag_name == 'path':
                            contents = doc.childNodes
                            datafile[tag_name] = getText(contents)
                            print '\t' + tag_name + ': ' \
                                + getText(contents)
                except:
                    pass

        print 'DONE EXP: ' + str(experiment.id)

        return experiment.id

