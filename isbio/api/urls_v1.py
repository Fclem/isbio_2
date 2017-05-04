from django.conf.urls import url, include
import views_v1 as v1

urlpatterns = [
	url(r'^$', v1.root, name='v1.root'),
	
	url(r'^project/news$', v1.news, name='v1.news'),
	url(r'^reports/?$', v1.reports, name='v1.reports'),
	url(r'^projects/?$', v1.projects, name='v1.projects'),
	url(r'^report_types/?$', v1.report_types, name='v1.report_types'),
	url(r'^users/?$', v1.users, name='v1.users'),
	url(r'^show/cache/?$', v1.show_cache, name='v1.show_cache'),
	url(r'^hook/', include('webhooks.urls'))
]
