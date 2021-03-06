# -*-*- encoding: utf-8 -*-*-

import os
import re
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback
import pprint

import webpage2html
import requests
from bs4 import BeautifulSoup

from flask import Blueprint, url_for, jsonify
from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError, register_blueprint
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions
from labmanager.rlms.queue import QueueTask, run_tasks

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class AmritaAddForm(AddForm):

    DEFAULT_URL = 'https://amrita.olabs.edu.in'
    DEFAULT_LOCATION = 'Coimbatore, India'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'amrita'
    DEFAULT_AUTOLOAD = True

    amrita_username  = TextField("Amrita username",        validators = [Required()])
    amrita_password  = PasswordField("Amrita password")


    def __init__(self, add_or_edit, *args, **kwargs):
        super(AmritaAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        old_configuration_dict = json.loads(old_configuration)
        new_configuration_dict = json.loads(new_configuration)
        if new_configuration_dict.get('amrita_password', '') == '':
            new_configuration_dict['amrita_password'] = old_configuration_dict.get('amrita_password','')
        return json.dumps(new_configuration_dict)

    def validate_amrita_password(form, field):
        if form.add_or_edit and field.data == '':
            raise ValidationError("This field is required.")


class AmritaFormCreator(BaseFormCreator):

    def get_add_form(self):
        return AmritaAddForm

def create_amrita_session(username, password):
    session = requests.Session()
    session.post("https://amrita.olabs.edu.in/?pg=bindex&bsub=login_page", data={'submit':'Login', 'username':username, 'password':password}, timeout=(30,30), verify=False)
    return session


class ObtainAmritaLabDataTask(QueueTask):
    def __init__(self, laboratory_id, username, password):
        self.username = username
        self.password = password
        self.result = {}
        super(ObtainAmritaLabDataTask, self).__init__(laboratory_id)

    def task(self):
        session = requests.Session()
        session.post("https://amrita.olabs.edu.in/?pg=bindex&bsub=login_page", data={'submit':'Login', 'username':self.username, 'password':self.password}, timeout=(30,30), verify=False)

        text = session.get(self.laboratory_id, timeout=(30,30), verify=False).text
        soup = BeautifulSoup(text, 'lxml')
        element = soup.find(text="Simulator")
        if not element:
            return

        a_element = None
        for parent in element.parents:
            if parent.name == 'a':
                a_element = parent
                break

        if not a_element:
            return

        simulator_link = a_element['href']
        if simulator_link.startswith('?'):
            simulator_link = 'http://' + urlparse.urlparse(self.laboratory_id).netloc + '/' + simulator_link
        soup_sim = BeautifulSoup(session.get(simulator_link, timeout=(30,30), verify=False).text, 'lxml')
        iframe = soup_sim.find("iframe")
        if not iframe:
            return

        iframe_url = iframe['src'].strip()
        if iframe_url.startswith('//'):
            iframe_url = 'http:{}'.format(iframe_url)

        if iframe_url.startswith('http://180.149.57.33/'):
            iframe_url = iframe_url.replace('180.149.57.33', 'www.olabs.edu.in')

        base_url, args = iframe_url.split('?', 1)
        args = '&'.join([ arg for arg in args.split('&') if arg.split('=')[0] not in ['elink_title', 'linktoken', 'elink_lan'] ])
        self.result = {
            'url' : base_url + '?' + args,
            'sim_url': simulator_link
        }

MIN_TIME = datetime.timedelta(hours=24)

def get_laboratories(username, password, force_cached=False):
    laboratories = AMRITA.global_cache.get('get_laboratories',  min_time = MIN_TIME)
    if laboratories:
        return laboratories

    if force_cached: # It should have returned already
        return None

    physics = 'https://www.olabs.edu.in/?pg=topMenu&id=40'
    biology = 'https://www.olabs.edu.in/?pg=topMenu&id=53'
    chemistry = 'https://www.olabs.edu.in/?pg=topMenu&id=41'

    all_category_urls = physics, biology, chemistry

    all_lab_links = {
        # url: name
    }

    lab_tasks = []

    session = create_amrita_session(username, password)

    for category_url in all_category_urls:
        text = session.get(category_url, timeout=(30, 30), verify=False).text
        soup = BeautifulSoup(text, 'lxml')
        for div_element in soup.find_all(class_='exptPadng'):
            for a_element in div_element.find_all('a'):
                inner_text = a_element.get_text().strip()
                if inner_text:
                    href = a_element['href']
                    if href.startswith('//'):
                        href = 'http:{}'.format(href)
                    all_lab_links[href] = inner_text
                    lab_tasks.append(ObtainAmritaLabDataTask(href, username, password))

    # for lab_task in lab_tasks:
    #     print(lab_task.laboratory_id)
    # import json
    # print(json.dumps(all_lab_links, indent=4))
        


    run_tasks(lab_tasks, threads=4)

    result = {
        'laboratories' : [],
        'all_links': [],
    }
    all_labs = []
    for task in lab_tasks:
        if task.result:
            name = all_lab_links[task.laboratory_id]
            iframe_url = task.result['url'] # TODO: remove linktoken
            sim_url = task.result['sim_url']

            lab = Laboratory(name=name, laboratory_id=iframe_url, description=name, home_url=sim_url)
            result['laboratories'].append(lab)
            result['all_links'].append({
                'lab': lab,
                'name': name,
                'base-url': task.laboratory_id,
                'sim-url': sim_url,
                'iframe-url': iframe_url,
            })

    AMRITA.global_cache['get_laboratories'] = result
    return result


FORM_CREATOR = AmritaFormCreator()

CAPABILITIES = [ Capabilities.WIDGET, Capabilities.URL_FINDER, Capabilities.CHECK_URLS, Capabilities.DOWNLOAD_LIST ]

class RLMS(BaseRLMS):

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')
        self.amrita_username = self.configuration.get('amrita_username', os.environ.get('AMRITA_USERNAME'))
        self.amrita_password = self.configuration.get('amrita_password', os.environ.get('AMRITA_PASSWORD'))
        if not self.amrita_username or not self.amrita_password:
            raise Exception("Invalid Amrita settings: credentials required")

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return CAPABILITIES 

    def get_laboratories(self, **kwargs):
        return get_laboratories(self.amrita_username, self.amrita_password)['laboratories']

    def get_base_urls(self):
        return [ 'http://amrita.olabs.edu.in', 'http://amrita.olabs.co.in', 'http://cdac.olabs.edu.in', 'https://amrita.olabs.edu.in', 'https://amrita.olabs.co.in', 'https://cdac.olabs.edu.in' ]

    def get_lab_by_url(self, url):
        if '?' in url:
            base_url, args = url.split('?', 1)
            args = '&'.join([ arg for arg in args.split('&') if arg.split('=')[0] not in ['elink_title', 'linktoken', 'elink_lan'] ])
            url = base_url + '?' + args

        laboratories = get_laboratories(self.amrita_username, self.amrita_password)
        for lab in laboratories['all_links']:
            if lab['sim-url'] == url or lab['iframe-url'] == url or lab['base-url'] == url:
                return lab['lab']
        return None

    def get_check_urls(self, laboratory_id):
        return [ laboratory_id ]

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        if '.co.in/' in laboratory_id:
            laboratory_id = laboratory_id.replace('.co.in/', '.edu.in/')

        if 'edu.in/' in laboratory_id:
            laboratory_id = laboratory_id.replace('http://', 'https://')

        response = {
            'reservation_id' : laboratory_id,
            'load_url' : laboratory_id
        }
        return response

    def load_widget(self, reservation_id, widget_name, **kwargs):
        if 'edu.in/' in reservation_id:
            reservation_id = reservation_id.replace('http://', 'https://')

        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]

    def get_downloads(self, laboratory_id):
        return {
            'en_ALL': url_for('amrita.amrita_download', laboratory_id=laboratory_id, _external=True),
        }


class AmritaTaskQueue(QueueTask):
    RLMS_CLASS = RLMS

def populate_cache(rlms):
    rlms.get_laboratories()

AMRITA = register("Amrita", ['1.0'], __name__)
AMRITA.add_local_periodic_task('Populating cache', populate_cache, hours = 13)

DEBUG = AMRITA.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

amrita_blueprint = Blueprint('amrita', __name__)

class FakeRequestsClass(object):
    def fake_get(self, *args, **kwargs):
        kwargs['verify'] = False
        return requests.get(*args, **kwargs)

    def __getattr__(self, name):
        if name == 'get':
            return self.fake_get
        return getattr(requests, name)

@amrita_blueprint.route('/ids')
def amrita_list():
    result = get_laboratories(os.environ.get('AMRITA_USERNAME'), os.environ.get('AMRITA_PASSWORD'), force_cached=True)
    if result is None:
        return jsonify(success=False, message="No lab found at all")

    labs = []
    for lab in result['laboratories']:
        labs.append({
            'laboratory_id': lab.laboratory_id,
            'name': lab.name,
            'description': lab.description,
            'home_url': lab.home_url,
        })
    return jsonify(success=True, labs=labs)
    

@amrita_blueprint.route('/id/<path:laboratory_id>')
def amrita_download(laboratory_id):
    result = get_laboratories(os.environ.get('AMRITA_USERNAME'), os.environ.get('AMRITA_PASSWORD'), force_cached=True)
    if result is None:
        return "Not found", 404

    laboratories = result['laboratories']
    link = None
    for lab in laboratories:
        if lab.laboratory_id == laboratory_id:
            link = lab.laboratory_id

    if link is None:
        laboratory_id = laboratory_id.replace('.co.in', '.edu.in')
        for lab in laboratories:
            if lab.laboratory_id == laboratory_id:
                link = lab.laboratory_id

    if link is None:
        laboratory_id = laboratory_id.replace('http://', 'https://')
        for lab in laboratories:
            if lab.laboratory_id == laboratory_id:
                link = lab.laboratory_id

    if link is None:
        laboratory_id = laboratory_id.replace('.edu.in', '.co.in')
        for lab in laboratories:
            if lab.laboratory_id == laboratory_id:
                link = lab.laboratory_id

    if not link:
        return "Not found", 404

    webpage2html.requests = FakeRequestsClass()
    try:
        generated = webpage2html.generate(index=link, keep_script=True, verbose=False, verify=False)
    finally:
        webpage2html.requests = requests
    return generated.encode()

register_blueprint(amrita_blueprint, url='/amrita')

def main():
    with CacheDisabler():
        rlms = RLMS("{}")
        t0 = time.time()
        laboratories = rlms.get_laboratories()
        tf = time.time()
        print len(laboratories), (tf - t0), "seconds"
        print
        print laboratories[:10]
        print
        # print rlms.reserve('http://phet.colorado.edu/en/simulation/beers-law-lab', 'tester', 'foo', '', '', '', '', locale = 'es_ALL')
    
        try:
            rlms.reserve('identifier-not-found', 'tester', 'foo', '', '', '', '', locale = 'xx_ALL')
        except LabNotFoundError:
            print "Captured error successfully"

        print rlms.get_base_urls()
        # print rlms.get_lab_by_url("https://phet.colorado.edu/en/simulation/acid-base-solutions")
    return

    for lab in laboratories[:5]:
        t0 = time.time()
        print rlms.reserve(lab.laboratory_id, 'tester', 'foo', '', '', '', '', locale = lang)
        tf = time.time()
        print tf - t0, "seconds"
    

if __name__ == '__main__':
    main()
