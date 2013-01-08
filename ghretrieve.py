import requests
import json
from urlparse import urljoin
from base64 import b64decode

def get_repo(repo_url):
    r = requests.get(repo_url)

    if(r.ok):
        repo_item = json.loads(r.text or r.content)
        return repo_item 

    return None

def get_repo_license(repo_url):
    license_files = {'COPYING':None, 'LICENSE':None}

    copying_url = "%s/contents/%s" % (repo_url, 'COPYING')
    license_url = "%s/contents/%s" % (repo_url, 'LICENSE')

    print "LICENSE url: %s" % license_url
    print "COPYING url: %s" % copying_url

    r = requests.get(copying_url)
    if(r.ok):
        copying_file = json.loads(r.text or r.content)
        license_files['COPYING'] = copying_file
    
    r = requests.get(license_url)
    if(r.ok):
        license_file = json.loads(r.text or r.content)
        license_files['LICENSE'] = license_file

    return license_files

if __name__ == "__main__":
    test_url = "https://api.github.com/repos/copiesofcopies/github-license-analysis"

    repo_item = get_repo(test_url)
    licenses = get_repo_license(test_url)

    if (licenses['COPYING']):
        print 'Repository "%s" has a COPYING file: %s' % (repo_item['full_name'], 
                                                                   b64decode(licenses['COPYING']['content']))
    elif (licenses['LICENSE']):
        print 'Repository "%s" has a LICENSE file: %s' % (repo_item['full_name'], 
                                                                   b64decode(licenses['LICENSE']['content']))
    else:
        print 'Repository "%s" has no top-level COPYING or LICENSE file' % repo_item['full_name']
