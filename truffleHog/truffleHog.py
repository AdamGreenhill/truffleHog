#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
import shutil
import sys
import math
import datetime
import argparse
import uuid
import hashlib
import tempfile
import os
import re
import json
import stat
from git import Repo
from git import NULL_TREE

def main():
    parser = argparse.ArgumentParser(description='Find secrets hidden in the depths of git.')
    parser.add_argument('--json', dest="output_json", action="store_true", help="Output in JSON")
    parser.add_argument("--regex", dest="do_regex", action="store_true", help="Enable high signal regex checks")
    parser.add_argument("--rules", dest="rules", help="Ignore default regexes and source from json file")
    parser.add_argument("--allow", dest="allow", help="Explicitly allow regexes from json list file")
    parser.add_argument("--entropy", dest="do_entropy", help="Enable entropy checks")
    parser.add_argument("--since_commit", dest="since_commit", help="Only scan from a given commit hash")
    parser.add_argument("--max_depth", dest="max_depth", help="The max commit depth to go back when searching for secrets")
    parser.add_argument("--branch", dest="branch", help="Name of the branch to be scanned")
    parser.add_argument('-i', '--include_paths', type=argparse.FileType('r'), metavar='INCLUDE_PATHS_FILE',
                        help='File with regular expressions (one per line), at least one of which must match a Git '
                             'object path in order for it to be scanned; lines starting with "#" are treated as '
                             'comments and are ignored. If empty or not provided (default), all Git object paths are '
                             'included unless otherwise excluded via the --exclude_paths option.')
    parser.add_argument('-x', '--exclude_paths', type=argparse.FileType('r'), metavar='EXCLUDE_PATHS_FILE',
                        help='File with regular expressions (one per line), none of which may match a Git object path '
                             'in order for it to be scanned; lines starting with "#" are treated as comments and are '
                             'ignored. If empty or not provided (default), no Git object paths are excluded unless '
                             'effectively excluded via the --include_paths option.')
    parser.add_argument("--repo_path", type=str, dest="repo_path", help="Path to the cloned repo. If provided, git_url will not be used")
    parser.add_argument("--cleanup", dest="cleanup", action="store_true", help="Clean up all temporary result files")
    parser.add_argument('git_url', type=str, help='URL for secret searching')
    parser.add_argument("--no-verify-ssl", dest="no_verify_ssl", action="store_true", help="Ignore SSL certificate warnings.")
    parser.set_defaults(regex=False)
    parser.set_defaults(rules={})
    parser.set_defaults(allow={})
    parser.set_defaults(max_depth=1000000)
    parser.set_defaults(since_commit=None)
    parser.set_defaults(entropy=True)
    parser.set_defaults(branch=None)
    parser.set_defaults(repo_path=None)
    parser.set_defaults(cleanup=False)
    parser.set_defaults(no_verify_ssl=False)
    args = parser.parse_args()
    rules = {}

    # Regexes

    regexes = {
        "Slack Token": "(xox[pborsa]-[0-9]{12}-[0-9]{12}-[0-9]{12}-[a-z0-9]{32})",
        "RSA private key": "-----BEGIN RSA PRIVATE KEY-----",
        "SSH (DSA) private key": "-----BEGIN DSA PRIVATE KEY-----",
        "SSH (EC) private key": "-----BEGIN EC PRIVATE KEY-----",
        "PGP private key block": "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        "AWS API Key": "((?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16})",
        "Amazon MWS Auth Token": "amzn\\.mws\\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "AWS API Key": "AKIA[0-9A-Z]{16}",
        "AWS AppSync GraphQL Key": "da2-[a-z0-9]{26}",
        "Facebook Access Token": "EAACEdEose0cBA[0-9A-Za-z]+",
        "Facebook OAuth": "[fF][aA][cC][eE][bB][oO][oO][kK].*['|\"][0-9a-f]{32}['|\"]",
        "GitHub": "[gG][iI][tT][hH][uU][bB].*['|\"][0-9a-zA-Z]{35,40}['|\"]",
        "Generic API Key": "[aA][pP][iI]_?[kK][eE][yY].*['|\"][0-9a-zA-Z]{32,45}['|\"]",
        "Generic Secret": "[sS][eE][cC][rR][eE][tT].*['|\"][0-9a-zA-Z]{32,45}['|\"]",
        "Google API Key": "AIza[0-9A-Za-z\\-_]{35}",
        "Google Cloud Platform API Key": "AIza[0-9A-Za-z\\-_]{35}",
        "Google Cloud Platform OAuth": "[0-9]+-[0-9A-Za-z_]{32}\\.apps\\.googleusercontent\\.com",
        "Google Drive API Key": "AIza[0-9A-Za-z\\-_]{35}",
        "Google Drive OAuth": "[0-9]+-[0-9A-Za-z_]{32}\\.apps\\.googleusercontent\\.com",
        "Google (GCP) Service-account": "\"type\": \"service_account\"",
        "Google Gmail API Key": "AIza[0-9A-Za-z\\-_]{35}",
        "Google Gmail OAuth": "[0-9]+-[0-9A-Za-z_]{32}\\.apps\\.googleusercontent\\.com",
        "Google OAuth Access Token": "ya29\\.[0-9A-Za-z\\-_]+",
        "Google YouTube API Key": "AIza[0-9A-Za-z\\-_]{35}",
        "Google YouTube OAuth": "[0-9]+-[0-9A-Za-z_]{32}\\.apps\\.googleusercontent\\.com",
        "Heroku API Key": "[hH][eE][rR][oO][kK][uU].*[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        "MailChimp API Key": "[0-9a-f]{32}-us[0-9]{1,2}",
        "Mailgun API Key": "key-[0-9a-zA-Z]{32}",
        "Password in URL": "[a-zA-Z]{3,10}://[^/\\s:@]{3,20}:[^/\\s:@]{3,20}@.{1,100}[\"'\\s]",
        "PayPal Braintree Access Token": "access_token\\$production\\$[0-9a-z]{16}\\$[0-9a-f]{32}",
        "Picatic API Key": "sk_live_[0-9a-z]{32}",
        "Slack Webhook": "https://hooks\\.slack\\.com/services/T[a-zA-Z0-9_]{8}/B[a-zA-Z0-9_]{8}/[a-zA-Z0-9_]{24}",
        "Stripe API Key": "sk_live_[0-9a-zA-Z]{24}",
        "Stripe Restricted API Key": "rk_live_[0-9a-zA-Z]{24}",
        "Square Access Token": "sq0atp-[0-9A-Za-z\\-_]{22}",
        "Square OAuth Secret": "sq0csp-[0-9A-Za-z\\-_]{43}",
        "Telegram Bot API Key": "[0-9]+:AA[0-9A-Za-z\\-_]{33}",
        "Twilio API Key": "SK[0-9a-fA-F]{32}",
        "Twitter Access Token": "[tT][wW][iI][tT][tT][eE][rR].*[1-9][0-9]+-[0-9a-zA-Z]{40}",
        "Twitter OAuth": "[tT][wW][iI][tT][tT][eE][rR].*['|\"][0-9a-zA-Z]{35,44}['|\"]"
    }

    for key in regexes:
        regexes[key] = re.compile(regexes[key])

    if args.rules:
        try:
            with open(args.rules, "r") as ruleFile:
                rules = json.loads(ruleFile.read())
                for rule in rules:
                    rules[rule] = re.compile(rules[rule])
        except (IOError, ValueError) as e:
            raise("Error reading rules file")
        for regex in dict(regexes):
            del regexes[regex]
        for regex in rules:
            regexes[regex] = rules[regex]
    allow = {}
    if args.allow:
        try:
            with open(args.allow, "r") as allowFile:
                allow = json.loads(allowFile.read())
                for rule in allow:
                    allow[rule] = read_pattern(allow[rule])
        except (IOError, ValueError) as e:
            raise("Error reading allow file")
    do_entropy = str2bool(args.do_entropy)

    # read & compile path inclusion/exclusion patterns
    path_inclusions = []
    path_exclusions = []
    if args.include_paths:
        for pattern in set(l[:-1].lstrip() for l in args.include_paths):
            if pattern and not pattern.startswith('#'):
                path_inclusions.append(re.compile(pattern))
    if args.exclude_paths:
        for pattern in set(l[:-1].lstrip() for l in args.exclude_paths):
            if pattern and not pattern.startswith('#'):
                path_exclusions.append(re.compile(pattern))

    output = find_strings(args.git_url, args.since_commit, args.max_depth, args.output_json, args.do_regex, do_entropy,
            surpress_output=False, custom_regexes=regexes, branch=args.branch, repo_path=args.repo_path, path_inclusions=path_inclusions, path_exclusions=path_exclusions, allow=allow, no_verify_ssl=args.no_verify_ssl)
    project_path = output["project_path"]
    if args.cleanup:
        clean_up(output)
    if output["foundIssues"]:
        sys.exit(1)
    else:
        sys.exit(0)

def read_pattern(r):
    if r.startswith("regex:"):
        return re.compile(r[6:])
    converted = re.escape(r)
    converted = re.sub(r"((\\*\r)?\\*\n|(\\+r)?\\+n)+", r"( |\\t|(\\r|\\n|\\\\+[rn])[-+]?)*", converted)
    return re.compile(converted)

def str2bool(v):
    if v == None:
        return True
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
HEX_CHARS = "1234567890abcdefABCDEF"

def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)

def shannon_entropy(data, iterator):
    """
    Borrowed from http://blog.dkbza.org/2007/05/scanning-data-for-entropy-anomalies.html
    """
    if not data:
        return 0
    entropy = 0
    for x in iterator:
        p_x = float(data.count(x))/len(data)
        if p_x > 0:
            entropy += - p_x*math.log(p_x, 2)
    return entropy


def get_strings_of_set(word, char_set, threshold=20):
    count = 0
    letters = ""
    strings = []
    for char in word:
        if char in char_set:
            letters += char
            count += 1
        else:
            if count > threshold:
                strings.append(letters)
            letters = ""
            count = 0
    if count > threshold:
        strings.append(letters)
    return strings

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def clone_git_repo(git_url, no_verify_ssl):
    project_path = tempfile.mkdtemp()

    if no_verify_ssl:
        Repo.clone_from(git_url, project_path, env={'GIT_SSL_NO_VERIFY' : '1'})
    else:
        Repo.clone_from(git_url, project_path)

    return project_path

def print_results(printJson, issue):
    commit_time = issue['date']
    branch_name = issue['branch']
    prev_commit = issue['commit']
    printableDiff = issue['printDiff']
    commitHash = issue['commitHash']
    reason = issue['reason']
    path = issue['path']

    if printJson:
        print(json.dumps(issue, sort_keys=True))
    else:
        print("~~~~~~~~~~~~~~~~~~~~~")
        reason = "{}Reason: {}{}".format(bcolors.OKGREEN, reason, bcolors.ENDC)
        print(reason)
        dateStr = "{}Date: {}{}".format(bcolors.OKGREEN, commit_time, bcolors.ENDC)
        print(dateStr)
        hashStr = "{}Hash: {}{}".format(bcolors.OKGREEN, commitHash, bcolors.ENDC)
        print(hashStr)
        filePath = "{}Filepath: {}{}".format(bcolors.OKGREEN, path, bcolors.ENDC)
        print(filePath)

        if sys.version_info >= (3, 0):
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name, bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit, bcolors.ENDC)
            print(commitStr)
            print(printableDiff)
        else:
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name.encode('utf-8'), bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit.encode('utf-8'), bcolors.ENDC)
            print(commitStr)
            print(printableDiff.encode('utf-8'))
        print("~~~~~~~~~~~~~~~~~~~~~")

def find_entropy(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash):
    stringsFound = []
    lines = printableDiff.split("\n")
    for line in lines:
        for word in line.split():
            base64_strings = get_strings_of_set(word, BASE64_CHARS)
            hex_strings = get_strings_of_set(word, HEX_CHARS)
            for string in base64_strings:
                b64Entropy = shannon_entropy(string, BASE64_CHARS)
                if b64Entropy > 4.5:
                    stringsFound.append(string)
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
            for string in hex_strings:
                hexEntropy = shannon_entropy(string, HEX_CHARS)
                if hexEntropy > 3:
                    stringsFound.append(string)
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
    entropicDiff = None
    if len(stringsFound) > 0:
        entropicDiff = {}
        entropicDiff['date'] = commit_time
        entropicDiff['path'] = blob.b_path if blob.b_path else blob.a_path
        entropicDiff['branch'] = branch_name
        entropicDiff['commit'] = prev_commit.message
        entropicDiff['diff'] = blob.diff.decode('utf-8', errors='replace')
        entropicDiff['stringsFound'] = stringsFound
        entropicDiff['printDiff'] = printableDiff
        entropicDiff['commitHash'] = prev_commit.hexsha
        entropicDiff['reason'] = "High Entropy"
    return entropicDiff

def regex_check(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash, custom_regexes={}):
    if custom_regexes:
        secret_regexes = custom_regexes
    else:
        secret_regexes = regexes
    regex_matches = []
    for key in secret_regexes:
        found_strings = secret_regexes[key].findall(printableDiff)
        for found_string in found_strings:
            found_diff = printableDiff.replace(printableDiff, bcolors.WARNING + str(found_string) + bcolors.ENDC)
        if found_strings:
            foundRegex = {}
            foundRegex['date'] = commit_time
            foundRegex['path'] = blob.b_path if blob.b_path else blob.a_path
            foundRegex['branch'] = branch_name
            foundRegex['commit'] = prev_commit.message
            foundRegex['diff'] = blob.diff.decode('utf-8', errors='replace')
            foundRegex['stringsFound'] = found_strings
            foundRegex['printDiff'] = found_diff
            foundRegex['reason'] = key
            foundRegex['commitHash'] = prev_commit.hexsha
            regex_matches.append(foundRegex)
    return regex_matches

def diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy, do_regex, printJson, surpress_output, path_inclusions, path_exclusions, allow):
    issues = []
    for blob in diff:
        printableDiff = blob.diff.decode('utf-8', errors='replace')
        if printableDiff.startswith("Binary files"):
            continue
        if not path_included(blob, path_inclusions, path_exclusions):
            continue
        for key in allow:
            printableDiff = allow[key].sub('', printableDiff)
        commit_time =  datetime.datetime.fromtimestamp(prev_commit.committed_date).strftime('%Y-%m-%d %H:%M:%S')
        foundIssues = []
        if do_entropy:
            entropicDiff = find_entropy(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash)
            if entropicDiff:
                foundIssues.append(entropicDiff)
        if do_regex:
            found_regexes = regex_check(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash, custom_regexes)
            foundIssues += found_regexes
        if not surpress_output:
            for foundIssue in foundIssues:
                print_results(printJson, foundIssue)
        issues += foundIssues
    return issues

def handle_results(output, output_dir, foundIssues):
    for foundIssue in foundIssues:
        result_path = os.path.join(output_dir, str(uuid.uuid4()))
        with open(result_path, "w+") as result_file:
            result_file.write(json.dumps(foundIssue))
        output["foundIssues"].append(result_path)
    return output

def path_included(blob, include_patterns=None, exclude_patterns=None):
    """Check if the diff blob object should included in analysis.

    If defined and non-empty, `include_patterns` has precedence over `exclude_patterns`, such that a blob that is not
    matched by any of the defined `include_patterns` will be excluded, even when it is not matched by any of the defined
    `exclude_patterns`. If either `include_patterns` or `exclude_patterns` are undefined or empty, they will have no
    effect, respectively. All blobs are included by this function when called with default arguments.

    :param blob: a Git diff blob object
    :param include_patterns: iterable of compiled regular expression objects; when non-empty, at least one pattern must
     match the blob object for it to be included; if empty or None, all blobs are included, unless excluded via
     `exclude_patterns`
    :param exclude_patterns: iterable of compiled regular expression objects; when non-empty, _none_ of the patterns may
     match the blob object for it to be included; if empty or None, no blobs are excluded if not otherwise
     excluded via `include_patterns`
    :return: False if the blob is _not_ matched by `include_patterns` (when provided) or if it is matched by
    `exclude_patterns` (when provided), otherwise returns True
    """
    path = blob.b_path if blob.b_path else blob.a_path
    if include_patterns and not any(p.match(path) for p in include_patterns):
        return False
    if exclude_patterns and any(p.match(path) for p in exclude_patterns):
        return False
    return True


def find_strings(git_url, since_commit=None, max_depth=1000000, printJson=False, do_regex=False, do_entropy=True, surpress_output=True,
                custom_regexes={}, branch=None, repo_path=None, path_inclusions=None, path_exclusions=None, allow={}, no_verify_ssl=False):
    output = {"foundIssues": []}
    if repo_path:
        project_path = repo_path
    else:
        project_path = clone_git_repo(git_url, no_verify_ssl)
    repo = Repo(project_path)
    already_searched = set()
    output_dir = tempfile.mkdtemp()

    if branch:
        branches = repo.remotes.origin.fetch(branch)
    else:
        branches = repo.remotes.origin.fetch()

    for remote_branch in branches:
        since_commit_reached = False
        branch_name = remote_branch.name
        prev_commit = None
        for curr_commit in repo.iter_commits(branch_name, max_count=max_depth):
            commitHash = curr_commit.hexsha
            if commitHash == since_commit:
                since_commit_reached = True
                break
            # if not prev_commit, then curr_commit is the newest commit. And we have nothing to diff with.
            # But we will diff the first commit with NULL_TREE here to check the oldest code.
            # In this way, no commit will be missed.
            diff_hash = hashlib.md5((str(prev_commit) + str(curr_commit)).encode('utf-8')).digest()
            if not prev_commit:
                prev_commit = curr_commit
                continue
            elif diff_hash in already_searched:
                prev_commit = curr_commit
                continue
            else:
                diff = prev_commit.diff(curr_commit, create_patch=True)
            # avoid searching the same diffs
            already_searched.add(diff_hash)
            foundIssues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy, do_regex, printJson, surpress_output, path_inclusions, path_exclusions, allow)
            output = handle_results(output, output_dir, foundIssues)
            prev_commit = curr_commit

        # Check if since_commit was used to check which diff should be grabbed
        if since_commit_reached:
            # Handle when there's no prev_commit (used since_commit on the most recent commit)
            if prev_commit is None:
                continue
            diff = prev_commit.diff(curr_commit, create_patch=True)
        else:
            diff = curr_commit.diff(NULL_TREE, create_patch=True)

        foundIssues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy, do_regex, printJson, surpress_output, path_inclusions, path_exclusions, allow)
        output = handle_results(output, output_dir, foundIssues)
    output["project_path"] = project_path
    output["clone_uri"] = git_url
    output["issues_path"] = output_dir
    if not repo_path:
        shutil.rmtree(project_path, onerror=del_rw)
    return output

def clean_up(output):
    issues_path = output.get("issues_path", None)
    if issues_path and os.path.isdir(issues_path):
        shutil.rmtree(output["issues_path"])

if __name__ == "__main__":
    main()
