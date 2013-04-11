# -*- coding: utf-8 -*-

import yaml
import re
import logging
import psycopg2
import sys
import os
import hashlib
import optparse
import subprocess
import csv
import shutil
from base64 import b64decode

config = None
db_conn = None
cur = None
nomos_re = re.compile(r"[^\)]*\)\s(.*)")

def create_metadata_table():
    # Create the license_metadata table if it doesn't already exist
    cur.execute("""CREATE TABLE license_metadata(id SERIAL PRIMARY KEY, 
                                license_id INT REFERENCES repository_licenses (id),
                                is_primary BOOLEAN DEFAULT FALSE,
                                license_abbr VARCHAR,
                                UNIQUE(license_id, license_abbr))""")

    db_conn.commit()


def process_licenses(start = 0):

    # Create a directory to store the license files in
    base_path = config['export_directory']

    if not os.path.exists(base_path):
        os.makedirs(base_path)

    # Get repo count
    cur.execute("""SELECT COUNT(*) FROM repositories""")
    count = cur.fetchone()[0]

    logger.info("There are %s repositories. Processing..." % count)

    end = start + 999

    while start < count:

        logger.info("Processing repositories %s - %s..." % (start, end))

	if not os.path.exists(base_path):
            os.makedirs(base_path)

        # Get license info
        cur.execute("""SELECT r.id, r.full_name, l.content, l.name, l.id
                   FROM repositories r, repository_licenses l
                   WHERE r.gh_id = l.repository_id
	 	     AND r.fork = 'f'
                     AND r.id >=%s AND r.id <= %s
                """ % (start, end))

        licenses = cur.fetchall()
	last_repo_path = None       
 
	for row in licenses:
            repo_id = row[0]
            repo_name = row[1]
            license_text = b64decode(row[2])
            license_name = row[3]
            license_id = row[4]

            repo_path = os.path.join(base_path, repo_name)
	
	    if last_repo_path != None and last_repo_path != repo_path:
	    	shutil.rmtree(last_repo_path)

	    last_repo_path = repo_path

            license_path = os.path.join(repo_path, license_name)

	    logger.info("Processing %s/%s (%s)..." % \
                (repo_name, license_name, repo_id,))

            if not os.path.exists(repo_path):
                os.makedirs(repo_path)

            if not os.path.exists(license_path):
                f = open(license_path, 'w')
                f.write(license_text)
                f.close()

            licenses_found = process_nomos_output(license_path)
            logger.info("%s/%s (%s) contains: %s" % \
                (repo_name, license_name, repo_id, ", ".join(licenses_found)))

	    os.remove(license_path)

            # Create a DB entry for each license found
            for abbr in licenses_found:
                try:
                    cur.execute("""
                            INSERT INTO license_metadata(license_id,
                                        license_abbr)
                                        VALUES ( %s, %s )
                            """, (license_id, abbr))

                    db_conn.commit()
                except psycopg2.IntegrityError, e:
                    db_conn.rollback()
                    logger.error('Integrity Error %s. Metadata record may '\
                                 'already exist for this project+license.' %\
                                 e)    
                except psycopg2.DatabaseError, e:
                    db_conn.rollback()
                
                    logger.error('Error %s when updating metadata for %s' %\
                                     (e, abbr))    
                    db_conn.close()
                    sys.exit(1)

        start = start + 1000
        end = end + 1000

        shutil.rmtree(base_path)

def export_licenses(output_file_path=None):

    cur.execute("""SELECT r.gh_id as repo_id, r.owner_login as github_user,
                          r.name as repo_name, r.description as repo_description,
                          r.private as repo_private, r.fork as repo_isfork, 
                          r.html_url as repo_url, l.name as license_filename, 
                          l.html_url as license_url, m.license_abbr as 
                          license_abbr, m.is_primary as license_isprimary
                     FROM repositories r
                     JOIN repository_licenses l 
                       ON r.gh_id = l.repository_id 
                     JOIN license_metadata m
                       ON l.id = m.license_id
                    WHERE m.license_abbr != 'No_license_found'
                 ORDER BY r.full_name
                   """) 

    licenses = cur.fetchall()

    for alicense in licenses:
        print "%s/%s: %s" % (alicense[0], alicense[1], alicense[2])


def process_nomos_output(license_path):

    nomos_path = config['nomos_path']
    exe = [nomos_path, license_path]

    for line in runProcess(exe):
        output = line.strip()

        if len(line.split()):
            m = nomos_re.match(output)

            if m:
                licenses_found = m.group(1).split(',')
                licenses_found = sanitize_license_list(licenses_found)
            else:
                licenses_found = []

            return licenses_found


def sanitize_license_list(license_list):

    # Find all the licenses we want to filter for
    ruby_i = list_search(license_list, "Ruby")
    pd_i = list_search(license_list, "Public-domain")
    mit_i = list_search(license_list, "MIT")
    mit_style_i = list_search(license_list, "MIT-style")
    artistic_i = list_search(license_list, "Artistic")
    fsf_i = list_search(license_list, "FSF")
    gpl_i = list_substring_search(license_list, "GPL")
    agpl_i = list_substring_search(license_list, "Affero")

    # All "MIT" and "MIT-style" should just be "MIT"
    if mit_i > -1 and mit_style_i > -1:
        license_list.remove("MIT-style")
    elif mit_style_i > -1:
        license_list[mit_style_i] = "MIT"

    # "Public domain" match for Ruby, Artistic & GPL(s) are usually
    # false positives
    if pd_i > -1 and (agpl_i > -1 or gpl_i > -1 \
                      or ruby_i > -1 or artistic_i > -1):
        license_list.remove("Public-domain")

    # "FSF" match for GPL not needed
    if gpl_i > -1 and fsf_i > -1:
        license_list.remove("FSF")

    return license_list


def list_search(alist, value):
    try:
        i = alist.index(value)
    except:
        i = -1

    return i

def list_substring_search(alist, search_substring):
    for item in alist:
        if re.search(search_substring, item):
            return alist.index(item)

    return -1


def runProcess(exe):    

    p = subprocess.Popen(exe, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    while(True):
      retcode = p.poll() #returns None while subprocess is running
      line = p.stdout.readline()
      yield line
      if(retcode is not None):
        break


def list_unmatched_repos():
    # List all of the repositories with no readme or license file

    cur.execute("""SELECT r.full_name, r.html_url
                   FROM repositories r
                   LEFT JOIN repository_licenses l 
                          ON r.gh_id = l.repository_id 
                   LEFT JOIN license_metadata m
                          ON l.id = m.license_id
                   WHERE m.license_id IS NULL""")

    licenses = cur.fetchall()

    for alicense in licenses:
        print "No match found for %s" % alicense[0]


def count_license_matches():
    # Count licenses matches

    cur.execute("""SELECT m.license_abbr, COUNT(r.id)
                   FROM repositories r
                   JOIN repository_licenses l 
                     ON r.gh_id = l.repository_id 
                   JOIN license_metadata m
                     ON l.id = m.license_id
               GROUP BY m.license_abbr, r.full_name
               ORDER BY m.license_abbr ASC""")

    licenses = cur.fetchall()

    for alicense in licenses:
        print "%s: %s" % ( alicense[0], alicense[1])


def map_repos_to_licenses():
    # Iterate through each repo's licenses, mapping each to an entry
    # in the master abbreviation list, dropping duplicate & equivalent
    # entries

    cur.execute("""SELECT id, license_abbr FROM licenses""")
    abbrs = cur.fetchall()
    lic_map = {}

    # Create a dict mapping license names to IDs
    for row in abbrs:
        lic_map[row[1]] = row[0]

    cur.execute("""SELECT r.id as rid, l.id as lid, m.license_abbr as abbr
                     FROM repositories r
                     JOIN repository_licenses l 
                       ON r.gh_id = l.repository_id 
                     JOIN license_metadata m
                       ON l.id = m.license_id
                    WHERE m.license_abbr != 'No_license_found'
                 ORDER BY r.id, l.id
                   """) 

    licenses = cur.fetchall()

    variant_re = re.compile(r"(.*)\-(style|possibility)")

    for alicense in licenses:
        r_id = alicense[0]
        l_id = alicense[1]
        l_abbr = alicense[2]

        variant = variant_re.match(l_abbr)
        license_id = None

        if variant:
            license_id = lic_map[m.group(1)]
        else:
            license_id = lic_map[l_abbr]

        logger.info('Associating repository %s with license %s' % (r_id, l_abbr))

        try:
            cur.execute("""
                        INSERT INTO repository_license_abbr(repository_id, license_id)
                             VALUES ( %s, %s )
                        """, (r_id, license_id))

            db_conn.commit()
        except psycopg2.IntegrityError, e:
            db_conn.rollback()
            logger.error('Integrity Error %s. License already associated with repo.' %\
                             e)    
        except psycopg2.DatabaseError, e:
            db_conn.rollback()
                
            logger.error('Error %s when associating repo %s with license %s' %\
                             (e, r_id, license_id))    
            db_conn.close()
            sys.exit(1)        


def list_multilicense_repos():
    # List repos for which more than one license was identified
    
    cur.execute("""SELECT r.full_name, COUNT(DISTINCT m.license_abbr) as lcount
                   FROM repositories r
                   JOIN repository_licenses l 
                     ON r.gh_id = l.repository_id
                   JOIN license_metadata m
                     ON l.id = m.license_id
               GROUP BY r.full_name
           HAVING COUNT(DISTINCT m.license_abbr) > 1
               ORDER BY lcount DESC
                 """)

    licenses = cur.fetchall()

    print "%s multilicensed repositories found:" % len(licenses)

    for alicense in licenses:
        print "%s licenses identified for %s" % ( alicense[1], alicense[0] )


if __name__ == "__main__":
    # Parse the yaml config file
    config_file = open('config.yaml', 'r')
    config = yaml.load(config_file.read())

    # Set up the command line argument parser
    parser = optparse.OptionParser()

    parser.add_option('-c', '--create-metadata-table',
                      action="store_true", dest="create_metadata_table",
                      help="""Create the license_metadata table""",
                      default="")

    parser.add_option('-i', '--identify_licenses',
                      action="store_true", dest="identify_licenses",
                      help="""(Re-)scan licenses for identifying strings, 
                              store results in metadata table""",
                      default="")

    parser.add_option('-e', '--export_licenses',
                      action="store_true", dest="export_licenses",
                      help="""Export CSV of license data for repositories""",
                      default="")

    parser.add_option('-p', '--process_licenses',
                      action="store_true", dest="process_licenses",
                      help="""Export licenses to disk, analyze them with
                              FOSSology nomos tool, store results""",
                      default="")

    parser.add_option('-s', '--start_with',
                      action="store", dest="start_with",
                      help="""Indicate which record to start processing with""",
                      default=0)

    parser.add_option('-l', '--list_unmatched_repos',
                      action="store_true", dest="list_unmatched_repos",
                      help="""List repositories for which there is no 
                              license match""",
                      default="")

    parser.add_option('-m', '--list_multilicense_repos',
                      action="store_true", dest="list_multilicense_repos",
                      help="""List repositories for which there are multiple 
                              license matches""",
                      default="")

    parser.add_option('-n', '--count_license_matches',
                      action="store_true", dest="count_license_matches",
                      help="""Count repositories that match each license""",
                      default="")

    parser.add_option('-a', '--map_repos_to_licenses',
                      action="store_true", dest="map_repos_to_licenses",
                      help="""Map repos to licenses""",
                      default="")
    
    options, args = parser.parse_args()

    # Initialize database connection
    db_conn = psycopg2.connect(database=config['static_database'], 
                           user=config['static_database_user'],
                           password=config['static_database_password'])    
    
    cur = db_conn.cursor()

    # Initialize log file
    logger = logging.getLogger(__name__)
    logging.basicConfig(filename='license_id.log',level=logging.ERROR)
    logging.getLogger(__name__).setLevel(logging.DEBUG)

    # Create the metadata table
    if options.create_metadata_table:
        create_metadata_table()

    # Try to match license files against known strings
    if options.identify_licenses:
        identify_licenses()

    # Try to match license files against known strings
    if options.process_licenses:
        process_licenses(int(options.start_with))

    # Export CSV of license data
    if options.export_licenses:
        export_licenses()

    # Print the unmatched files
    if options.list_unmatched_repos:
        list_unmatched_repos()

    # Print repos with multiple licenses
    if options.list_multilicense_repos:
        list_multilicense_repos()

    # Count license occurences
    if options.count_license_matches:
        count_license_matches()

    # Map repos to licenses
    if options.map_repos_to_licenses:
        map_repos_to_licenses()
