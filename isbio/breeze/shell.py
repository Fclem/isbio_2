import django
from django.template.defaulttags import now
import os, shutil, re, stat, copy
from datetime import datetime
from multiprocessing import Process
import xml.etree.ElementTree as xml
from Bio import Entrez
from django.template.defaultfilters import slugify
from django.conf import settings
from django.core.files import File, base
import breeze.models
import auxiliary as aux
import logging
import pickle, json
import hashlib
from django.utils import timezone
from datetime import timedelta
import socket
from breeze.models import Report, Jobs
from exceptions import Exception
#from views import breeze

if socket.gethostname().startswith('breeze'):
	import drmaa

logger = logging.getLogger(__name__)


def init_script(name, inline, person):
	spath = str(settings.MEDIA_ROOT) + str(get_folder_name("scripts", name, None))

	if not os.path.isdir(spath):
		os.makedirs(spath)
		dbitem = breeze.models.Rscripts(name=name,
										category=breeze.models.Script_categories.objects.get(category="general"),
										inln=inline, author=person, details="empty", order=0)

		# create empty files for header, code and xml
		dbitem.header.save('name.txt', base.ContentFile('# write your header here...'))
		dbitem.code.save('name.r', base.ContentFile('# copy and paste main code here...'))
		dbitem.save()

		root = xml.Element('rScript')
		root.attrib['ID'] = str(dbitem.id)
		input_array = xml.Element('inputArray')
		input_array.text = "empty"
		root.append(input_array)

		newxml = open(str(settings.TEMP_FOLDER) + 'script_%s.xml' % (person), 'w')
		xml.ElementTree(root).write(newxml)
		newxml.close()

		dbitem.docxml.save('script.xml', File(open(str(settings.TEMP_FOLDER) + 'script_%s.xml' % (person))))
		dbitem.save()
		os.remove(str(settings.TEMP_FOLDER) + 'script_%s.xml' % (person))
		# dbitem.docxml.save('name.xml', base.ContentFile(''))

		return spath

	return False


def init_pipeline(form):
	"""
        Initiates a new RetortType item in the DB.
        Creates a folder for initial pipeline data.
    """
	# First Save the data that comes with a form:
	# 'type', 'description', 'search', 'access'
	new_pipeline = form.save()

	# Add configuration file
	new_pipeline.config.save('config.txt', base.ContentFile('#          Configuration Module  \n'))
	new_pipeline.save()

	return True


def update_script_dasics(script, form):
	"""
        Update script name and its inline description. In case of a new name it
        creates a new folder for script and makes file copies but preserves db istance id
    """

	if str(script.name) != str(form.cleaned_data['name']):
		new_folder = str(settings.MEDIA_ROOT) + str(get_folder_name("scripts", str(form.cleaned_data['name']), None))
		old_folder = str(settings.MEDIA_ROOT) + str(get_folder_name("scripts", script.name, None))
		new_slug = slugify(form.cleaned_data['name'])

		if not os.path.isdir(new_folder):
			os.makedirs(new_folder)
			script.name = form.cleaned_data['name']
			script.inln = form.cleaned_data['inline']
			script.save()
			# copy folder
			files_list = os.listdir(old_folder)
			for item in files_list:
				fileName, fileExtension = os.path.splitext(item)
				# shutil.copy2(old_folder + item, str(new_folder) + str(new_slug) + str(fileExtension))
				if fileExtension == '.xml':
					script.docxml.save('name.xml', File(open(old_folder + item)))
				elif fileExtension == '.txt':
					script.header.save('name.txt', File(open(old_folder + item)))
				elif fileExtension == '.r' or fileExtension == '.R':
					script.code.save('name.r', File(open(old_folder + item)))
				else:
					script.logo.save('name' + str(fileExtension), File(open(old_folder + item)))

			# delete old folder
			shutil.rmtree(old_folder)

			script.creation_date = datetime.now()
			script.save()
		return True
	else:
		script.inln = form.cleaned_data['inline']
		script.creation_date = datetime.now()
		script.save()
		return True


def update_script_description(script, post_data):
	script.details = str(post_data['description_field'])
	script.creation_date = datetime.now()
	script.save()
	return True


def update_script_xml(script, xml_data):
	file_path = str(settings.MEDIA_ROOT) + str(script.docxml)

	if os.path.isfile(file_path):
		handle = open(file_path, 'w')
		handle.write(str(xml_data))
		handle.close()

		script.creation_date = datetime.now()
		script.save()
		return True
	else:
		return False


def update_script_sources(script, post_data):
	if post_data['source_file'] == 'Header':
		file_path = settings.MEDIA_ROOT + str(script.header)
	elif post_data['source_file'] == 'Main':
		file_path = settings.MEDIA_ROOT + str(script.code)

	handle = open(file_path, 'w')
	handle.write(str(post_data['mirrorEditor']))
	handle.close()

	script.creation_date = datetime.now()
	script.save()
	return True


def update_script_logo(script, pic):
	if script.logo:
		os.remove(str(settings.MEDIA_ROOT) + str(script.logo))

	script.logo = pic
	script.creation_date = datetime.now()
	script.save()
	return True


def del_script(script):
	folder = str(settings.MEDIA_ROOT) + str(get_folder_name("scripts", script.name, None))

	if os.path.isdir(folder):
		shutil.rmtree(folder)
		script.delete()
		return True

	return False


def del_pipe(pipe):
	slug = slugify(str(pipe.id) + '_' + pipe.type)
	folder = str(settings.MEDIA_ROOT) + 'pipelines/%s/' % (slug)

	if os.path.isdir(folder):
		shutil.rmtree(folder)
		pipe.delete()
		return True

	return False


def del_report(report):
	report.delete()


def del_job(job):
	docxml_path = str(settings.MEDIA_ROOT) + str(get_folder_name('jobs', job.jname, job.juser.username))

	if os.path.isdir(docxml_path):
		shutil.rmtree(docxml_path)
	job.delete()
	return True


def schedule_job(job, mailing):
	"""
        Creates SGE configuration file for QSUB command
    """
	job_path = str(settings.MEDIA_ROOT) + str(get_folder_name('jobs', job.jname, job.juser.username))
	config_path = job_path + slugify(job.jname + '_' + job.juser.username) + '_config.sh'
	config = open(config_path, 'w')

	st = os.stat(config_path)  # config should be executble
	os.chmod(config_path, st.st_mode | stat.S_IEXEC)

	command = '#!/bin/bash \n' + str(settings.R_ENGINE_PATH) + 'CMD BATCH --no-save ' + str(settings.MEDIA_ROOT) + str(
		job.rexecut)
	config.write(command)
	config.close()

	job.progress = 0
	job.save()
	return 1


def run_job(job, script=None):
	"""
		Submits scripts as an R-job to cluster with qsub (SGE);
		This submission implements SCRIPTS concept in BREEZE
		(For REPOTS submission see run_report)
	"""
	log = logger.getChild('run_report')
	assert isinstance(log, logging.getLoggerClass())
	assert isinstance(job, Jobs)

	try:
		loc = str(settings.MEDIA_ROOT) + str(get_folder_name('jobs', job.jname, job.juser.username))
		config = loc + slugify(job.jname + '_' + job.juser.username) + '_config.sh'

		default_dir = os.getcwd()
		os.chdir(loc)

		# prevents db being dropped
		django.db.close_connection()

		job.status = "queued_active"
		job.breeze_stat = "prepare_run"
		job.progress = 15
		job.save()
		log.info('j' + str(job.id) + ' : creating job')

	except Exception as e:
		log.exception('j' + str(job.id) + ' : pre-run error ' + str(e))
		log.error('j' + str(job.id) + ' : process unexcpectedly terminated')

	try:
		s = drmaa.Session()
		s.initialize()
		jt = s.createJobTemplate()
		assert isinstance(jt, object)

		jt.workingDirectory = loc
		jt.jobName = slugify(job.jname) + '_JOB'
		# external mail address support
		# Not working ATM probably because of mail backend not being properly configured
		if job.email != '':
			jt.email = [str(job.email), str(job.juser.email)]
		else:
			jt.email = [str(job.juser.email)]
		# print "Mail address for this job is : " +  ', '.join(jt.email)
		# mail notification on events
		if job.mailing != '':
			jt.nativeSpecification = "-m " + job.mailing  # Begin End Abort Suspend
		jt.blockEmail = False
		jt.remoteCommand = config
		jt.joinFiles = True

		job.progress = 25
		#job.status = 'submission'
		#job.save()
		log.info('j' + str(job.id) + ' : triggering dramaa.runJob')
		job.sgeid = s.runJob(jt)
		log.info('j' + str(job.id) + ' : returned sgedid "' + str(job.sgeid) + '"')
		job.progress = 30
		job.save()

		SGEID = copy.deepcopy(job.sgeid)
		# waiting for the job to end
		#if not SGEID:
		#print "no id!"
		# TODO have a closer look into that
		log.info('j' + str(job.id) + ' : stat : ' + str(s.jobStatus(job.sgeid)))
		retval = s.wait(SGEID, drmaa.Session.TIMEOUT_WAIT_FOREVER)
		job.progress = 100
		job.save()

		if retval.hasExited and retval.exitStatus == 0:
			job.status = 'succeed'
			log.info('j' + str(job.id) + ' : dramaa.runJob ended with exit code 0 !')
			# clean up the folder
		else:
			log.error('j' + str(job.id) + ' : dramaa.runJob ended with exit code ' + str(retval.exitStatus))
			job = Jobs.objects.get(id=job.id)  # make sure data is updated
			if job.status != 'aborted':
				pass
				job.status = 'failed'  # seems to interfere with aborting process TODO check

		job.save()
		s.exit()
		os.chdir(default_dir)

		#track_sge_job(job, True)

		log.info('j' + str(job.id) + ' : process terminated successfully !')
		return True
	except (drmaa.AlreadyActiveSessionException, drmaa.InvalidArgumentException, drmaa.InvalidJobException, drmaa.NoActiveSessionException) as e:
		# TODO improve this part
		log.exception('j' + str(job.id) + ' : drmaa error ' + str(e))
		log.error('j' + str(job.id) + ' : process unexcpectedly terminated')
		# job.status = "failed"
		job.progress = 67
		job.save()
		s.exit()
		return e
	except Exception as e:
		# report.status = 'failed'
		log.exception('r' + str(job.id) + ' : drmaa unknow error ' + str(e))
		log.error('r' + str(job.id) + ' : process unexcpectedly terminated')
		job.progress = 66
		job.save()

		s.exit()
		return False
	#except e:
		# job.status = 'failed'
		#job.progress = 100
		#job.save()

		#newfile = open(str(settings.TEMP_FOLDER) + 'job_%s_%s.log' % (job.juser, job.jname), 'w')
		#newfile.write("UNKNOW ERROR" + vars(e))
		#newfile.close()

		#s.exit()
	#return False


# THIS should run in a separate Process
# TODO merge those two functions
def run_report(report):
	"""
        Submits reports as an R-job to cluster with SGE;
        This submission implements REPORTS concept in BREEZE
        (For SCRIPTS submission see run_job)
    """
	try:
		log = logger.getChild('run_report')
		assert isinstance(log, logging.getLoggerClass())

		loc = str(settings.MEDIA_ROOT) + report.home
		config = loc + '/sgeconfig.sh'
		default_dir = os.getcwd()
		os.chdir(loc)

		fmFlag = report.fm_flag

		if fmFlag:
			os.system(settings.JDBC_BRIDGE_PATH)

		# prevents db being dropped
		django.db.close_connection()
		report.breeze_stat = "prepare_run"
		report.status = "queued_active"
		report.progress = 15
		report.save()

		log.info('r' + str(report.id) + ' : creating job')
	except Exception as e:
		log.exception('r' + str(report.id) + ' : pre-run error ' + str(e))
		log.error('r' + str(report.id) + ' : process unexcpectedly terminated')

	try:
		s = drmaa.Session()
		s.initialize()

		jt = s.createJobTemplate()

		jt.workingDirectory = loc
		jt.jobName = slugify(report.name) + '_REPORT'
		jt.email = [str(report.author.email)]
		jt.blockEmail = False

		jt.remoteCommand = config
		jt.joinFiles = True
		jt.nativeSpecification = "-m bea"

		report.progress = 25
		#report.status = 'submission'
		#report.save()
		log.info('r' + str(report.id) + ' : triggering dramaa.runJob')
		report.sgeid = s.runJob(jt)
		log.info('r' + str(report.id) + ' : returned sgedid "' + str(report.sgeid) + '"')
		report.progress = 30
		report.save()
		log.info('r' + str(report.id) + ' : stat : ' + str(s.jobStatus(report.sgeid)) )
		# waiting for the job to end
		SGEID = copy.deepcopy(report.sgeid)
		retval = s.wait(SGEID, drmaa.Session.TIMEOUT_WAIT_FOREVER)
		report.progress = 100
		report.save()

		print retval

		"""
		JobInfo(jobId='5146654', hasExited=True, hasSignal=False, terminatedSignal='SIGunknown signal', hasCoreDump=False, wasAborted=False, exitStatus=0, resourceUsage={'exit_status': '0.0000', 'ru_inblock': '0.0000', 'io': '0.0195', 'acct_maxvmem': '1615011840.0000', 'ru_nvcsw': '3780.0000', 'maxvmem': '1615011840.0000', 'ru_isrss': '0.0000', 'ru_stime': '1.4118', 'ru_nsignals': '0.0000', 'priority': '0.0000', 'mem': '19.2431', 'ru_nivcsw': '1821.0000', 'acct_iow': '0.0000', 'acct_io': '0.0195', 'acct_cpu': '17.2444', 'acct_mem': '19.2431', 'iow': '0.0000', 'start_time': '1434979102.0000', 'ru_msgsnd': '0.0000', 'ru_wallclock': '25.0000', 'ru_minflt': '121285.0000', 'submission_time': '1434979101.0000', 'ru_utime': '15.8326', 'ru_oublock': '0.0000', 'ru_nswap': '0.0000', 'ru_majflt': '0.0000', 'signal': '0.0000', 'vmem': '0.0000', 'ru_ixrss': '0.0000', 'ru_ismrss': '0.0000', 'end_time': '1434979127.0000', 'ru_idrss': '0.0000', 'ru_maxrss': '279540.0000', 'ru_msgrcv': '0.0000', 'cpu': '17.2444'})

		"""

		if retval.hasExited:
			if retval.exitStatus == 0:
				log.info('r' + str(report.id) + ' : dramaa.runJob ended with exit code 0 !')
				report.status = 'succeed'
				# clean up the folder
			else:
				log.error('r' + str(report.id) + ' : dramaa.runJob ended with exit code ' + str(retval.exitStatus))
				report = Report.objects.get(id=report.id)  # make sure data is updated
				if report.status != 'aborted':
					pass
					report.status = 'failed'  # seems to interfere with aborting process TODO check

		report.save()

		# aux.open_folder_permissions(loc, 0777)

		os.chdir(default_dir)

		if fmFlag:
			extra_path = loc + "/transfer_to_fm.txt"
			extra_file = open(extra_path, 'r')
			command = extra_file.read()
			run = command.split("\"")[1]
			os.system(run)
		s.exit()

		# track_sge_job(report, True)

	except (drmaa.AlreadyActiveSessionException, drmaa.InvalidArgumentException, drmaa.InvalidJobException) as e:
		# TODO improve this part
		log.exception('r' + str(report.id) + ' : drmaa error ' + str(e))
		log.error('r' + str(report.id) + ' : process unexcpectedly terminated')
		report.progress = 67
		report.save()
		s.exit()
		return False
	except Exception as e:
		# report.status = 'failed'
		log.exception('r' + str(report.id) + ' : drmaa unknow error ' + str(e))
		log.error('r' + str(report.id) + ' : process unexcpectedly terminated')
		report.progress = 66
		report.save()

		s.exit()
		return False

	log.info('r' + str(report.id) + ' : process terminated successfully !')
	return True


# TODO check that
def abort_report(report):
	stat = report.status
	ret = False
	try:
		if report.sgeid != "" and report.sgeid is not None:
			s = drmaa.Session()
			s.initialize()
			report.status = "aborted"
			s.control(report.sgeid, drmaa.JobControlAction.TERMINATE)
		else:
			report.status = "aborted"

	except drmaa.AlreadyActiveSessionException:
		# TODO improve this part
		if settings.DEBUG: print("AlreadyActiveSessionException")
		report.status = stat
		ret = "unable to abort"
	except drmaa.InvalidJobException:
		if settings.DEBUG: print("InvalidJobException")
		report.status = "aborted"
		ret = "InvalidJobException"
	except drmaa.InvalidArgumentException:
		report.status = "aborted"
		ret = "InvalidArgumentException"

	report.save()

	try:
		s.exit()
	except Exception as e:
		pass

	if ret:
		return ret
	return True


decodestatus = {
	drmaa.JobState.UNDETERMINED: 'process status cannot be determined',
	drmaa.JobState.QUEUED_ACTIVE: 'job is queued and active',
	drmaa.JobState.SYSTEM_ON_HOLD: 'job is queued and in system hold',
	drmaa.JobState.USER_ON_HOLD: 'job is queued and in user hold',
	drmaa.JobState.USER_SYSTEM_ON_HOLD: 'job is queued and in user and system hold',
	drmaa.JobState.RUNNING: 'job is running',
	'active': 'job is running',
	drmaa.JobState.SYSTEM_SUSPENDED: 'job is system suspended',
	drmaa.JobState.USER_SUSPENDED: 'job is user suspended',
	drmaa.JobState.DONE: 'job finished normally',
	'succeed': 'job finished normally',
	drmaa.JobState.FAILED: 'job finished, but failed',
	'aborted': 'job has been aborted',
	'init': 'job instance is being generated (if you see this more than 1 min please contact admin)',
	'scheduled': 'job is saved for later submission',
	'checking': '...',
	'submission': '...'
}


# TODO redesign / rewrite entirely
def track_sge_job(job, force_refresh=False):
	log = logger.getChild('track_sge_job')
	type = 'r' if isinstance(job, Report) else 'j'
	assert isinstance(log, logging.getLoggerClass())

	changed = False
	# force_refresh = False

	status = str(job.status)
	message = type + str(job.id) + ' : status : ' + status
	log.debug(message)

	if status != 'succeed' and status != 'aborted' and status != drmaa.JobState.FAILED and job.sgeid != "":
		if job.breeze_stat == 'init' or job.breeze_stat == 'prepare_run':
			force_refresh = True
			job.breeze_stat = 'refreshing'
			job.save()

		if force_refresh:
			try:
				s = drmaa.Session()
				s.initialize()

				status = str(s.jobStatus(job.sgeid))
				log.info(type + str(job.id) + ' : drmaa says ' + str(job.status))
			except drmaa.InvalidArgumentException:
				log.exception(type + str(job.id) + ' : drmaa InvalidArgumentException')
				if settings.DEBUG: print("InvalidArgumentException")
			except drmaa.InvalidJobException:
				log.exception(type + str(job.id) + ' : drmaa InvalidJobException -> FAILING job')
				if settings.DEBUG: print("InvalidJobException")
				status = "failed"
			except drmaa.AlreadyActiveSessionException:  # this is OK, since a child process is in a drmaa session monitoring the job
				if settings.DEBUG: print("AlreadyActiveSessionException")
				log.warning(type + str(job.id) + ' : drmaa AlreadyActiveSessionException')
			else:
				try:
					s.exit()
				except Exception as e:
					pass
	elif job.status != 'scheduled':
		now_t = timezone.now()  # .time()
		if isinstance(job, Jobs):
			crea = job.staged
		elif isinstance(job, Report):
			crea = job.created
		tdelta = now_t - crea
		assert isinstance(tdelta, timedelta)
		log.warning(type + str(job.id) + ' : sgeid has been empty for ' + str(tdelta.seconds) + ' sec')
		if settings.DEBUG: print(type + str(job.id) + ' sgeid has been empty for ' + str(tdelta.seconds) + ' sec')
		if tdelta > timedelta(seconds=settings.NO_SGEID_EXPIRY):
			log.warning(type + str(job.id) + ' : aborting due to NO sgeid')
			if settings.DEBUG: print(type + str(job.id) + ' : aborting due to NO sgeid')
			status = 'aborted'
			job.sgeid = 0
			job.progress = 100
			changed = True

	if status != job.status and job.status != 'failed' and job.status != 'aborted' and job.status != 'succeed':
		job.status = status
		changed = True

	if job.status == drmaa.JobState.QUEUED_ACTIVE and job.progress != 35:
		job.progress = 35
		changed = True
	elif (job.status == "active" or status == drmaa.JobState.RUNNING) and job.progress != 55:
		job.status = "active"
		job.progress = 55
		changed = True
	elif (job.status == drmaa.JobState.DONE or job.status == drmaa.JobState.FAILED) and job.progress != 100:
		job.progress = 100
		changed = True

	if changed:
		job.save()
		# job.update(progress=job.progress, status=job.status, sgeid=job.sgeid)
		log.info(message + ' changed to ' + str(job.status))

	log.debug(type + str(job.id) + ' : nwstat : ' + str(job.status))

	return decodestatus[job.status]


# 29/05/2015
def track_sge_job_bis(jobs, force_refresh=False):
	log = logger.getChild('track_sge_job_bis')

	assert isinstance(log, logging.getLoggerClass())

	if jobs == list():
		return

	stats = dict()

	try:
		s = drmaa.Session()
		s.initialize()

		for job in jobs:

			type = 'r' if isinstance(job, Report) else 'j'
			changed = False

			status = str(job.status)
			message = type + str(job.id) + ' : status : ' + status
			log.debug(message)

			if job.sgeid != "":
				if job.breeze_stat == 'init' or job.breeze_stat == 'prepare_run':
					force_refresh = True
					job.breeze_stat = 'refreshing'
					job.save()

				if force_refresh and status != 'succeed' and status != 'aborted' and status != drmaa.JobState.FAILED:
					status = str(s.jobStatus(job.sgeid))
					log.info(type + str(job.id) + ' : drmaa says ' + str(job.status))
			elif job.status != 'scheduled':
				now_t = timezone.now()  # .time()
				if isinstance(job, Jobs):
					crea = job.staged
				elif isinstance(job, Report):
					crea = job.created
				tdelta = now_t - crea
				assert isinstance(tdelta, timedelta)
				log.warning(type + str(job.id) + ' : sgeid has been empty for ' + str(tdelta.seconds) + ' sec')
				if settings.DEBUG: print(
					type + str(job.id) + ' sgeid has been empty for ' + str(tdelta.seconds) + ' sec')
				if tdelta > timedelta(seconds=settings.NO_SGEID_EXPIRY):
					log.warning(type + str(job.id) + ' : aborting due to NO sgeid')
					if settings.DEBUG: print(type + str(job.id) + ' : aborting due to NO sgeid')
					status = 'aborted'
					job.sgeid = 0
					job.progress = 100
					changed = True

			if status != job.status and job.status != 'failed' and job.status != 'aborted' and job.status != 'succeed':
				job.status = status
				changed = True

			if job.status == drmaa.JobState.QUEUED_ACTIVE and job.progress != 35:
				job.progress = 35
				changed = True
			elif (job.status == "active" or status == drmaa.JobState.RUNNING) and job.progress != 55:
				job.status = "active"
				job.progress = 55
				changed = True
			elif (job.status == drmaa.JobState.DONE or job.status == drmaa.JobState.FAILED) and job.progress != 100:
				job.progress = 100
				changed = True

			if changed:
				job.save()
				log.info(message + ' changed to ' + str(job.status))

			log.debug(type + str(job.id) + ' : nwstat : ' + str(job.status))

			# stats.update({job.id: decodestatus[job.status]})

	except drmaa.InvalidArgumentException:
		log.exception('all : drmaa InvalidArgumentException')
		if settings.DEBUG: print("InvalidArgumentException")
	except drmaa.InvalidJobException:
		log.exception('all : drmaa InvalidJobException')
		if settings.DEBUG: print("InvalidJobException")
	except drmaa.AlreadyActiveSessionException:  # this is OK, since a child process is in a drmaa session monitoring the job
		if settings.DEBUG: print("AlreadyActiveSessionException")
		log.warning('all : drmaa AlreadyActiveSessionException')
	except Exception as e:
		log.error('all : unhandeled drmaa error : ' + str(e))

	try:
		s.exit()
	except Exception as e:
		log.error('while exiting drmaa instance : ' + str(e))
		pass

	return jobs


def assemble_job_folder(jname, juser, tree, data, code, header, FILES):
	"""
        Builds (singe) R-exacutable file: puts together sources, header
        and input parameters from user
    """

	# create job folder
	directory = get_job_folder(jname, juser)
	if not os.path.exists(directory):
		os.makedirs(directory)

	rexec = open(str(settings.TEMP_FOLDER) + 'rexec.r', 'w')
	script_header = open(str(settings.MEDIA_ROOT) + str(header), "rb").read()
	script_code = open(str(settings.MEDIA_ROOT) + str(code), "rb").read()

	params = ''
	for item in tree.getroot().iter('inputItem'):
		item.set('val', str(data.cleaned_data[item.attrib['comment']]))
		if item.attrib['type'] == 'CHB':
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(
				data.cleaned_data[item.attrib['comment']]).upper() + '\n'
		elif item.attrib['type'] == 'NUM':
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(
				data.cleaned_data[item.attrib['comment']]) + '\n'
		elif item.attrib['type'] == 'TAR':
			lst = re.split(', |,|\n|\r| ', str(data.cleaned_data[item.attrib['comment']]))
			seq = 'c('
			for itm in lst:
				if itm != "":
					seq = seq + '\"%s\",' % itm
			seq = seq[:-1] + ')'
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(seq) + '\n'
		elif item.attrib['type'] == 'FIL' or item.attrib['type'] == 'TPL':
			add_file_to_job(jname, juser, FILES[item.attrib['comment']])
			params = params + str(item.attrib['rvarname']) + ' <- "' + str(
				data.cleaned_data[item.attrib['comment']]) + '"\n'
		elif item.attrib['type'] == 'DTS':
			path_to_datasets = str(settings.MEDIA_ROOT) + "datasets/"
			slug = slugify(data.cleaned_data[item.attrib['comment']]) + '.RData'
			params = params + str(item.attrib['rvarname']) + ' <- "' + str(path_to_datasets) + str(slug) + '"\n'
		elif item.attrib['type'] == 'MLT':
			res = ''
			seq = 'c('
			for itm in data.cleaned_data[item.attrib['comment']]:
				if itm != "":
					res += str(itm) + ','
					seq = seq + '\"%s\",' % itm
			seq = seq[:-1] + ')'
			item.set('val', res[:-1])
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(seq) + '\n'
		else:  # for text, text_are, drop_down, radio
			params = params + str(item.attrib['rvarname']) + ' <- "' + str(
				data.cleaned_data[item.attrib['comment']]) + '"\n'

	tree.write(str(settings.TEMP_FOLDER) + 'job.xml')

	rexec.write("setwd(\"%s\")\n" % directory)
	rexec.write("#####################################\n")
	rexec.write("###       Code Section            ###\n")
	rexec.write("#####################################\n")
	rexec.write(script_code)
	rexec.write("\n\n#####################################\n")
	rexec.write("### Parameters Definition Section ###\n")
	rexec.write("#####################################\n")
	rexec.write(params)
	rexec.write("\n\n#####################################\n")
	rexec.write("###       Assembly Section        ###\n")
	rexec.write("#####################################\n")
	rexec.write(script_header)

	rexec.close()
	return 1


def build_header(data):
	header = open(str(settings.TEMP_FOLDER) + 'header.txt', 'w')
	string = str(data)
	header.write(string)
	header.close()
	return header


def add_file_to_report(directory, f):
	if not os.path.exists(directory):
		os.makedirs(directory)

	with open(directory + "/" + f.name, 'wb+') as destination:
		for chunk in f.chunks():
			destination.write(chunk)


def add_file_to_job(job_name, user_name, f):
	directory = get_job_folder(job_name, user_name)

	if not os.path.exists(directory):
		os.makedirs(directory)

	with open(directory + f.name, 'wb+') as destination:
		for chunk in f.chunks():
			destination.write(chunk)


def get_job_folder(name, user=None):
	return str(settings.MEDIA_ROOT) + str(get_folder_name('jobs', name, user))


def get_folder_name(loc, name, user=None):
	if loc == "jobs":
		slug = slugify(name + '_' + str(user))
	else:
		slug = slugify(name)
	return '%s/%s/' % (loc, slug)


def get_dataset_info(path):
	path = str(settings.MEDIA_ROOT) + str(path)
	lst = list()

	# r('library(vcd)')
	#    r.assign('dataset', str(path))
	#    r('load(dataset)')
	#    r('dataSet1 <- sangerSet[1:131,]')
	#    drugs = r('featureNames(dataSet1)')
	#
	#    for pill in drugs:
	#        lst.append(dict(name=str(pill), db="Sanger.RData"))

	return lst


def gen_params_string(docxml, data, dir, files):
	"""
        Iterates over script's/tag's parameters to bind param names and user input;
        Produces a (R-specific) string with one parameter definition per lines,
        so the string can be pushed directly to R file.
    """
	tmp = dict()
	params = str()
	for item in docxml.getroot().iter('inputItem'):
		if item.attrib['type'] == 'CHB':
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(
				data.get(item.attrib['comment'], "NA")).upper() + '\n'
		elif item.attrib['type'] == 'NUM':
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(data.get(item.attrib['comment'], "NA")) + '\n'
		elif item.attrib['type'] == 'TAR':
			lst = re.split(', |,|\n|\r| ', str(data.get(item.attrib['comment'], "NA")))
			seq = 'c('
			for itm in lst:
				if itm != "":
					seq = seq + '\"%s\",' % itm

			if lst == ['']:
				seq = seq + ')'
			else:
				seq = seq[:-1] + ')'
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(seq) + '\n'
		elif item.attrib['type'] == 'FIL' or item.attrib['type'] == 'TPL':

			if files:
				try:
					add_file_to_report(dir, files[item.attrib['comment']])
					params = params + str(item.attrib['rvarname']) + ' <- "' + str(
						files[item.attrib['comment']].name) + '"\n'
				except:
					pass
			else:
				params = params + str(item.attrib['rvarname']) + ' <- ""\n'
		elif item.attrib['type'] == 'DTS':
			path_to_datasets = str(settings.MEDIA_ROOT) + "datasets/"
			slug = slugify(data.get(item.attrib['comment'], "NA")) + '.RData'
			params = params + str(item.attrib['rvarname']) + ' <- "' + str(path_to_datasets) + str(slug) + '"\n'
		elif item.attrib['type'] == 'MLT':
			res = ''
			seq = 'c('
			for itm in data.getlist(item.attrib['comment'], "NA"):
				if itm != "":
					res += str(itm) + ','
					seq = seq + '\"%s\",' % itm
			seq = seq[:-1] + ')'
			item.set('val', res[:-1])
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(seq) + '\n'
		elif item.attrib['type'] == 'DTM_SAMPLES':
			res = ''
			seq = 'c('
			for itm in data.getlist(item.attrib['comment'], "NA"):
				if itm != "":
					res += str(itm) + ','
					seq = seq + '\"%s\",' % itm
			seq = seq[:-1] + ')'
			item.set('val', res[:-1])
			params = params + '# First character of each element in the vector below\n# serves to distinguish Group (G) and Sample (S) Ids;\n# ! You have to trim each element to get original Id !\n'
			params = params + str(item.attrib['rvarname']) + ' <- ' + str(seq) + '\n'
		elif item.attrib['type'] == 'SCREEN_GROUPS':
			res = ''
			seq = 'c('
			for itm in data.getlist(item.attrib['comment'], "NA"):
				if itm != "":
					res += str(itm) + ','
					seq = seq + '\"%s\",' % itm
			seq = seq[:-1] + ')'
			item.set('val', res[:-1])
			params = params + '# This shows the selected screen group IDs!\n'
			params = params + "Screen_groups" + ' <- ' + str(seq) + '\n'
		# params = params + "Screen_groups" + ' <- "' + str(data.get(item.attrib['comment'], "NA")) + '"\n'
		else:  # for text, text_are, drop_down, radio
			params = params + str(item.attrib['rvarname']) + ' <- "' + str(
				data.get(item.attrib['comment'], "NA")) + '"\n'

	return params


def report_search(data_set, report_type, query):
	"""
        Each report type assumes its own search implementation;
        RPy2 could be a good option (use local installation on VM):
            - each report is assosiated with an r-script for searching;
            - each report should have another r-script to generate an overview
    """
	lst = list()

	# !!! HANDLE EXCEPTIONS IN THIS FUNCTION !!! #

	# GENE - Entrez search with BioPython #
	if str(report_type) == 'Gene' and len(query) > 0:
		Entrez.email = "dmitrii.bychkov@helsinki.fi"  # <- bring user's email here
		instance = str(query) + '[Gene/Protein Name]'  # e.g. 'DMPK[Gene/Protein Name]'
		species = 'Homo sapiens[Organism]'
		search_query = instance + ' AND ' + species
		handle = Entrez.esearch(db='gene', term=search_query)
		record = Entrez.read(handle)

		for item in record['IdList']:
			record_summary = Entrez.esummary(db='gene', id=item)
			record_summary = Entrez.read(record_summary)
			if record_summary[0]['Name']:
				lst.append(
					dict(id=str(record_summary[0]['Id']), name=str(record_summary[0]['Name']), db='Entrez[Gene]'))

	# Other report types should be implemented in a generalized way! #
	else:
		pass

	return lst


def get_report_overview(report_type, instance_name, instance_id):
	"""
        Most likely will call rCode to generate overview in order
        to separate BREEZE and report content.
    """
	summary_srting = str()

	if str(report_type) == 'Drug' and len(instance_name) > 0:
		summary_srting = ""

	if str(report_type) == 'Gene' and len(instance_name) > 0:
		if instance_id is not None:
			record_summary = Entrez.esummary(db="gene", id=instance_id)
			record_summary = Entrez.read(record_summary)

			if record_summary[0]["NomenclatureName"]:
				summary_srting += record_summary[0]["NomenclatureName"]
			if record_summary[0]["Orgname"]:
				summary_srting += " [" + record_summary[0]["Orgname"] + "] "
		else:
			summary_srting = "Instance ID is missing!"

	return summary_srting


def dump_project_parameters(project, report):
	dump = '# <----------  Project Details  ----------> \n'
	dump += 'report.author          <- \"%s\"\n' % report.author.username
	dump += 'report.pipeline        <- \"%s\"\n' % report.type
	dump += 'project.name           <- \"%s\"\n' % project.name
	dump += 'project.manager        <- \"%s\"\n' % project.manager
	dump += 'project.pi             <- \"%s\"\n' % project.pi
	dump += 'project.author         <- \"%s\"\n' % project.author
	dump += 'project.collaborative  <- \"%s\"\n' % project.collaborative
	dump += 'project.wbs            <- \"%s\"\n' % project.wbs
	dump += 'project.external.id    <- \"%s\"\n' % project.external_id
	dump += '# <----------  end of Project Details  ----------> \n\n'

	return copy.copy(dump)


def dump_pipeline_config(report_type, query_key):
	dump = ''

	config_path = str(settings.MEDIA_ROOT) + str(report_type.config)
	dump += '# <----------  Pipeline Config  ----------> \n'
	dump += 'query.key          <- \"%s\"  # id of queried RORA instance \n' % query_key
	dump += open(config_path, 'r').read() + '\n'
	dump += '# <------- end of Pipeline Config --------> \n\n\n'

	return copy.copy(dump)


def build_report(report_data, request_data, report_property, sections):
	""" Assembles report home folder, configures DRMAA and R related files
		and spawns a new process for reports DRMAA job on cluster.

	:param report_data: report info dictionary
	:type report_data: dict
	:param request_data: a copy of request object
	:type request_data: HTTPrequest
	:param report_property: report property form
	:type report_property: breezeForms.ReportPropsForm
	:param sections: a list of 'Rscripts' db objects
	:type sections: list
	:return: True
	:rtype: bool
	"""

	from breeze.models import Project, UserProfile, ReportType, Report
	from django.contrib.auth.models import User
	log = logger.getChild('build_report')
	assert isinstance(log, logging.getLoggerClass())
	assert isinstance(request_data.user, User)
	# 'report_name' - report's headline

	# get the request ReportType
	rt = ReportType.objects.get(type=report_data['report_type'])

	# shared_users = breeze.models.OrderedUser.objects.filter(username__in=buddies)
	shared_users = aux.extract_users(request_data.POST.get('Groups'), request_data.POST.get('Individuals'))

	if shared_users == list() and request_data.POST.get('shared'):
		shared_users = request_data.POST.getlist('shared')

	the_user = request_data.user
	the_user.prof = UserProfile.objects.get(user=the_user)
	assert isinstance(the_user.prof, UserProfile)

	# create initial instance so that we can use its db id
	dbitem = Report(
		type=rt,
		name=str(report_data['instance_name']),
		author=the_user,
		progress=1,
		project=Project.objects.get(id=request_data.POST.get('project')),
		institute=the_user.prof.institute_info,
		status='',
		rora_id=report_data['instance_id'],
		breeze_stat='init'
	)
	dbitem.save()
	# Now that it has an id we can use m2m ref
	if shared_users:
		dbitem.shared = shared_users

	# BUILD R-File
	dbitem.generate_R_file(sections, request_data)

	# configure shell-file
	config_path = dbitem.get_home + '/sgeconfig.sh'
	config = open(config_path, 'w')

	# config should be executable
	st = os.stat(config_path)
	os.chmod(config_path, st.st_mode | stat.S_IEXEC)

	command = '#!/bin/bash \n%sCMD BATCH --no-save %s' % (settings.R_ENGINE_PATH, dbitem.r_exec_path)
	config.write(command)
	config.close()

	# open report's folder for others
	st = os.stat(dbitem.get_home)
	os.chmod(dbitem.get_home, st.st_mode | stat.S_IRWXG)

	# clem : saves parameters into db, in order to be able to duplicate report
	dbitem.conf_params = pickle.dumps(request_data.POST)
	if request_data.FILES:
		tmp = dict()
		for each in request_data.FILES:
			tmp[str(each)] = str(request_data.FILES[each])
		dbitem.conf_files = json.dumps(tmp)
	dbitem.save()

	# generate shiny access for offsite users
	if report_data['report_type'] == 'ScreenReport':
		dbitem.generate_shiny_key()

	dbitem.breeze_stat = 'run_wait'
	dbitem.save()

	return True
