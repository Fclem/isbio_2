from . import code_v1 as code
from .common import *

# import json # included in common
# import time # included in common
# from django.http import HttpResponse # included in common
# from django.core.handlers.wsgi import WSGIRequest # included in common
# from django.core.exceptions import SuspiciousOperation # included in common
# from breeze.utilities import * # included in common
# from breeze.utils import pp
# from django.core.urlresolvers import reverse
# from django.conf import settings
# from django import http
# from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
# from django.template.context import RequestContext
# from django.contrib.auth.decorators import login_required
# from django.http import HttpResponseRedirect, HttpResponsePermanentRedirect


#########
# VIEWS #
#########


# clem 17/10/2016
def hook(_):
	return code.get_response()


# clem 17/10/2016
@csrf_exempt
def reload_sys(request):
	payload, rq = code.get_git_hub_json(request)
	if payload:
		# print('GitReload event header: "%s"' % rq.event_name)
		# print('size of payload : %s, type : %s' % (len(payload), type(payload)))
		allow_filter = {
			'ref': settings.GIT_AUTO_REF
		}
		if check_filter(payload, allow_filter):
			result = code.do_self_git_pull()
			return get_response(result, payload)
		
	raise default_suspicious(request)
	
	
# clem 17/10/2016
@csrf_exempt
def git_hook(request):
	payload, rq = code.get_git_hub_json(request)
	if payload:
		# print('GitHook event header: "%s"' % rq.event_name)
		# print('size of payload : %s, type : %s' % (len(payload), type(payload)))
		allow_filter = {
		}
		if check_filter(payload, allow_filter):
			result = code.do_r_source_git_pull()
			if not result:
				return get_response_opt(http_code=HTTP_NOT_IMPLEMENTED)
			else:
				return get_response(data=payload)
	
	raise default_suspicious(request)