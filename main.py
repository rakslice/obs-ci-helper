import base64
import collections
import json
import logging
import os
import sys
import urllib
import urllib2
from xml.dom.minidom import parse, Element

"""
Get build results (success/failure/error) from an openbuildservice job,
and put the statuses onto the corresponding commits in github
"""

OBS_STATUS_BUILDING = "building"
OBS_STATUS_SUCCESS = "succeeded"
OBS_STATUS_FAILED = "failed"

GITHUB_STATUS_SUCCESS = "success"
GITHUB_STATUS_FAILED = "failure"
GITHUB_STATUS_OTHER = "error"


def pretty_print(obj):
    print json.dumps(obj, indent=4, sort_keys=True)


class XMLEndpoint(object):
    def __init__(self, base_url, authorization):
        if base_url.endswith("/"):
            base_url = base_url[:-1]
        self.base_url = base_url
        self.authorization = authorization

    def _request_prep(self, request):
        """:type request: urllib2.Request"""
        request.add_header("Authorization", self.authorization)

    def get(self, relative_url):
        """
        :type relative_url: str
        :rtype: xml.dom.minidom.Document
        """
        url = self.base_url + relative_url
        print url
        request = urllib2.Request(url)
        self._request_prep(request)
        handle = urllib2.urlopen(request)
        try:
            doc = parse(handle)
            return doc
        finally:
            handle.close()


class BuildRevisionInfo(object):
    def __init__(self, revision, api):
        """
        :type api: OpenBuildServiceAPI
        """
        self._revision = revision
        self._xml_jobhists = {}
        """:type: dict[(str, str), xml.dom.minidom.Element]"""
        self._api = api

    def add_jobhist(self, repo, arch, xml_jobhist):
        """":type xml_jobhist: xml.dom.minidom.Element"""
        key = self._create_key(repo, arch)
        if key in self._xml_jobhists:
            msg = "duplicate entries for repo %s arch %s" % (repo, arch)
            print msg
            print "old:"
            print self._xml_jobhists[key].toprettyxml()
            print "new:"
            print xml_jobhist.toprettyxml()

            assert False, msg
        self._xml_jobhists[key] = xml_jobhist

    @staticmethod
    def _create_key(repo, arch):
        return repo, arch

    def get_status(self):
        code_counts = collections.Counter()

        other_count = 0

        normal_codes = frozenset([OBS_STATUS_BUILDING, OBS_STATUS_SUCCESS, OBS_STATUS_FAILED])

        for entry in self._xml_jobhists.values():
            code = entry.getAttribute("code")
            code_counts[code] += 1
            if code not in normal_codes:
                other_count += 1

        if code_counts[OBS_STATUS_BUILDING] > 0:
            return OBS_STATUS_BUILDING
        if other_count > 0:
            return "unknown"
        if code_counts[OBS_STATUS_FAILED] > 0:
            return OBS_STATUS_FAILED
        if code_counts[OBS_STATUS_SUCCESS] > 0:
            return OBS_STATUS_SUCCESS

        return "no results"

    def get_git_revision(self):
        it = self._xml_jobhists.itervalues()
        some_result = iter(it).next()
        assert isinstance(some_result, Element)
        versrel = some_result.getAttribute("versrel")
        print "versrel " + versrel
        trailing_num_str = versrel.rsplit("-", 1)[-1]
        assert trailing_num_str.isdigit()
        rest = versrel[:-(len(trailing_num_str) + 1)]
        return rest.rsplit(".", 1)[-1]

    @staticmethod
    def get_build_log_url():
        # TODO if obs one day allows access to historical build logs...
        return ""


class OpenBuildServiceAPI(object):
    def __init__(self, settings):
        self.project = settings["project"]
        self.package = settings["package"]

        authorization = "Basic %s" % base64.b64encode("%s:%s" % (settings["username"], settings["password"]))
        self.endpoint = XMLEndpoint("https://api.opensuse.org", authorization)

    def context(self):
        return "%s_%s" % (self.project, self.package)

    def description(self):
        return "project %s package %s" % (self.project, self.package)

    def get_directory(self, relative_url):
        """:rtype: list of unicode"""
        out = []
        doc = self.endpoint.get(relative_url)
        for entry in doc.getElementsByTagName("entry"):
            assert isinstance(entry, Element)
            out.append(entry.getAttribute("name"))
        return out

    def get_project_repositories(self, project):
        return self.get_directory("/build/%s" % urllib.quote_plus(project))

    def get_repository_arches(self, project, cur_repo):
        return self.get_directory("/build/%s/%s" % (urllib.quote_plus(project), urllib.quote_plus(cur_repo)))

    def get_job_history_entries(self, project, cur_repo, arch, package, limit=10):
        """:rtype: list of xml.dom.minidom.Element"""
        doc = self.endpoint.get("/build/%s/%s/%s/_jobhistory?%s" % (urllib.quote_plus(project),
                                                                    urllib.quote_plus(cur_repo),
                                                                    urllib.quote_plus(arch),
                                                                    urllib.urlencode({"package": package,
                                                                                      "limit": str(limit)}),
                                                                    ))
        return doc.getElementsByTagName("jobhist")

    def get_latest_build_revisions(self):
        """
        :rtype: list of BuildRevisionInfo
        """

        repositories = self.get_project_repositories(self.project)

        build_info_by_revision = {}
        """:type: dict[str,BuildRevisionInfo]"""

        limit = 20

        print "Fetching the last %d OBS build logs" % limit

        for cur_repo in repositories:
            for cur_arch in self.get_repository_arches(self.project, cur_repo):

                jobhists = self.get_job_history_entries(self.project, cur_repo, cur_arch, self.package, limit=limit)

                for cur_jobhist in jobhists:

                    srcmd5 = cur_jobhist.getAttribute("srcmd5")
                    start_time = cur_jobhist.getAttribute("starttime")
                    revision = "%s_%s" % (srcmd5, start_time)

                    if revision not in build_info_by_revision:
                        build_info_by_revision[revision] = BuildRevisionInfo(revision, self)

                    build_info_by_revision[revision].add_jobhist(cur_repo, cur_arch, cur_jobhist)

        revisions = build_info_by_revision.keys()
        revisions.sort(reverse=True)
        return [build_info_by_revision[rev] for rev in revisions]


class GitHubAPI(object):
    def __init__(self, settings):
        self.token = settings["token"]
        self.owner = settings["owner"]
        self.repo = settings["repo"]

    def _api_post(self, relative_url, post_params_obj):
        data_json = json.dumps(post_params_obj)
        request = urllib2.Request("https://api.github.com" + relative_url, data=data_json)
        request.add_header("Authorization", "token %s" % self.token)
        request.get_method = lambda: "POST"
        request.add_header("Content-type", "application/json")
        try:
            handle = urllib2.urlopen(request)
        except urllib2.HTTPError, e:
            print e.read()
            raise
        try:
            response_data = handle.read()
        finally:
            handle.close()

        return json.loads(response_data)

    def set_build_status(self, git_revision, status, link_url, context, description):
        url = "/repos/%s/%s/statuses/%s" % (urllib.quote(self.owner), urllib.quote(self.repo), urllib.quote(git_revision))
        status_obj = {
            "state": status,
            "target_url": link_url,
            "context": context,
            "description": description
        }
        print "POST " + url
        pretty_print(status_obj)
        result = self._api_post(url,
                                status_obj)
        pretty_print(result)


def ishex(s):
    """:type s: str"""
    for ch in s:
        if ch.isdigit() or "a" <= ch <= "f" or "A" <= ch <= "F":
            continue
        return False
    return True


class OpenBuildServiceCIHelper(object):
    def __init__(self, settings):
        self.already_processed_revisions = set()
        self.obs_api = OpenBuildServiceAPI(settings["obs"])
        self.github_api = GitHubAPI(settings["github"])
        self.logger = logging.getLogger("OpenBuildServiceCIHelper")
        self.logger.level = logging.INFO
        self.logger.addHandler(logging.StreamHandler(sys.stdout))

    def update_cycle(self):
        build_revisions = self.obs_api.get_latest_build_revisions()

        self.logger.info("Finding any unprocessed revisions")
        for revision in build_revisions:
            git_revision = revision.get_git_revision()

            if not ishex(git_revision):
                self.logger.info("skipping revision with %s instead of git commit" % git_revision)
                continue
            if len(git_revision) < 40:
                self.logger.info("skipping partial git revision %s" % git_revision)
                continue
            if git_revision in self.already_processed_revisions:
                self.logger.info("skipping already-processed revision %s" % git_revision)
                continue

            self.logger.info("Processing %s", git_revision)

            obs_status = revision.get_status()

            if obs_status == OBS_STATUS_BUILDING:
                self.logger.info("Revision %s is still building", git_revision)
                continue
            elif obs_status == OBS_STATUS_SUCCESS:
                status_for_github = GITHUB_STATUS_SUCCESS
            elif obs_status == OBS_STATUS_FAILED:
                status_for_github = GITHUB_STATUS_FAILED
            else:
                status_for_github = GITHUB_STATUS_OTHER

            self.logger.info("Saving github status for %s (%s)", git_revision, status_for_github)

            link_url = revision.get_build_log_url()

            context = "obs_" + self.obs_api.context()
            description = "obs %s was %s" % (self.obs_api.description(), status_for_github)
            self.github_api.set_build_status(git_revision, status_for_github, link_url, context, description)

            self.already_processed_revisions.add(git_revision)


script_path = os.path.dirname(os.path.abspath(__file__))


def load_json(filename):
    with open(filename, "r") as handle:
        return json.load(handle)


def save_json(filename, obj):
    head, tail = os.path.split(filename)
    filename_new = os.path.join(head, "~" + tail)
    with open(filename_new, "w") as handle:
        json.dump(obj, handle)
    if sys.platform == "win32" and os.path.exists(filename):
        os.remove(filename)
    os.rename(filename_new, filename)


def main():
    # TODO explore ways this could operate other than a one-shot run

    settings = load_json(os.path.join(script_path, "settings.json"))
    state_filename = os.path.join(script_path, "state.json")
    if os.path.exists(state_filename):
        state = load_json(state_filename)
    else:
        state = {"done_revisions": []}

    obj = OpenBuildServiceCIHelper(settings)
    obj.already_processed_revisions = set(state["done_revisions"])

    obj.update_cycle()

    state["done_revisions"] = list(obj.already_processed_revisions)
    save_json(state_filename, state)


if __name__ == "__main__":
    main()
