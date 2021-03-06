Deployment practices
====================

The target platform of OpenSpending is Ubuntu Lucid Lynx (10.04) LTS. While
the software can be installed on other systems (OS X is used on a daily 
basis, Windows may be a stretch), the following guide will refer to 
dependencies by their Ubuntu package name.

Tomcat-based multi-core Solr
''''''''''''''''''''''''''''

Warning: as of the time of writing, Ubuntu contains a solr package,
but this is an old version and largely unmaintained. Avoid it;
performance is likely to be disappointing.

Install tomcat 7, and configure it to run on a suitable port (look for
a <Connector> element in /etc/tomcat7/server.xml).

Download solr and unpack it.

Copy the example/solr directory and the webapp to a suitable location

  $ cp apache-solr-3.5.0/example/solr . -a
  $ cp apache-solr-3.5.0/dist/apache-solr-3.5.0.war solr.war

Create /etc/tomcat7/Catalina/localhost/solr.xml with the following contents:

   <?xml version="1.0" encoding="utf-8"?>
   <Context docBase="/home/okfn/openspending/solr.war" debug="0" crossContext="true">
     <Environment name="solr/home" type="java.lang.String" value="/home/okfn/openspending/solr" override="true"/>
   </Context>

Adjust the paths to point to the files copied above.

Create the directory for storing the solr index, and set permissions
for tomcat to access it:

  $ mkdir solr/data
  $ chown tomcat7 solr/data

Edit solr/conf/solrconfig.xml in the directory created above, and change:

 - <dataDir> from a variable to an explicit path, like:

     <dataDir>/home/okfn/openspending/solr/data</dataDir>

 - Comment out this line:

     <queryResponseWriter name="velocity" class="solr.VelocityResponseWriter" enable="${solr.velocity.enabled:true}"/>


Installing the software
'''''''''''''''''''''''

This guide is intended as a complement to :doc:`install`, so a basic
familiarity with the installation procedure and configuration options is
assumed. The key differences in a production install are these:

* We usually install OpenSpending as user ``okfn`` in ``~/var/srvc/<site>``,
  where the installation root is a ``virtualenv``.
* As a database, we'll always use PostgreSQL (version 9.1 for production).
  This also means we need to install the ``psycopg2`` python bindings used
  by SQLALchemy. The server is installed and set up by creating a user and 
  initial database::
    
    # apt-get install postgres
    # su postgres
    $ createuser -D -P -R -S openspending
    Password:
    $ createdb -E utf8 -O openspending -T template0 openspending.org

* To install the core software and dependencies, a pip file is created as
  ``pip-site.txt`` with the following contents::

    psycopg2
    gunicorn
    -e git+http://github.com/okfn/openspending#egg=openspending

  This means that updates can be installed easily and quickly by running
  the same command used for the initial setup::

    (env)~/var/srvc/openspending.org$ pip install -r pip-site.txt

* Set up related git modules, help, and catalogs in src/openspending, as in :doc:`install`.

* Set up site.ini as in :doc:`install`, with additional attention paid to:

  openspending.migrate_dir must point to src/openspending/miration
  inside the virtualenv, if site.ini is not in that directory

* Set up the database:

  $ ostool site.ini db init

* Create the session storage directory, and set up permissions:

  $ mkdir .pylons_data
  $ chown www-data .pylons_data

* The application is run through ``gunicorn`` (Green Unicorn), a fast, 
  pre-fork based HTTP server for WSGI applications. The application provides
  special support for pastescript so that it can be started via a simple
  prompt::

    (env)~/var/srvc/openspending.org$ gunicorn_paster site.ini

  (Where site.ini is your primary configuration file.) To determine the 
  number of workers and the port to listen on, a configuration file called
  ``gunicorn-config.py`` is created with basic settings::

    import multiprocessing
    bind = "127.0.0.1:18000"
    workers = multiprocessing.cpu_count() * 2 + 1

  This can be passed using the ``-c`` argument::

    (env)~/var/srvc/openspending.org$ gunicorn_paster -c gunicorn-config.py site.ini

* In order to make sure gunicorn is automatically started, monitored, and run
  with the right arguments, ``supervisord`` is installed::

    # apt-get install supervisor

  After installing supervisor, a new configuration file can be dropped into 
  ``/etc/supervisor/conf.d/openspending.org.conf`` with the following basic
  contents::

    [program:openspending.org]
    command=/home/okfn/var/srvc/openspending.org/bin/gunicorn_paster /home/okfn/var/www/openspending.org/site.ini -c /home/okfn/var/srvc/openspending.org/gunicorn-config.py
    directory=/home/okfn/var/srvc/openspending.org/
    user=www-data
    autostart=true
    autorestart=true
    stdout_logfile=/home/okfn/var/srvc/openspending.org/logs/supervisord.log
    redirect_stderr=true

  For logging, this required that you create the logs directory in the site 
  install, with permissions for ``www-data`` to write it.

  Supervisor can be started as a daemon::

    # /etc/init.d/supervisor start

* Finally, ``nginx`` is used as a front-end web server through which the
  application is proxied and static files are served. Install ``nginx`` as 
  a normal package::

    # apt-get install nginx

  A configuration can be created at ``/etc/nginx/sites-available/openspending``
  and later symlinked over into the ``sites-enabled`` folder. The host will 
  contain a server name, static path and a reference to the upstream
  ``gunicorn`` server::

      upstream app_server {
        server 127.0.0.1:18000;
      }

      server {
        listen 80;
        server_name openspending.org;

        access_log /var/log/nginx/openspending.org-access.log;
        error_log /var/log/nginx/openspending.org-error.log notice;

        root /home/okfn/var/srvc/openspending.org/src/openspending/openspending/ui/public;

        location /static {
          alias /home/okfn/var/srvc/openspending.org/src/openspending/openspending/ui/public/static;
        }

        location / {
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header Host $http_host;
          proxy_redirect off;
          proxy_pass http://app_server;
          break;
        }
      }

  In a completely unexpected turn of events, ``nginx`` can be started 
  as a daemon::

    # /etc/init.d/nginx start
