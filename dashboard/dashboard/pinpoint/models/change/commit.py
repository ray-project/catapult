# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections

from dashboard.common import namespaced_stored_object
from dashboard.services import gitiles_service


_REPOSITORIES_KEY = 'repositories'


class NonLinearError(Exception):
  """Raised when trying to find the midpoint of Changes that are not linear."""


class Commit(collections.namedtuple('Commit', ('repository', 'git_hash'))):
  """A git repository pinned to a particular commit."""

  def __str__(self):
    """Returns an informal short string representation of this Commit."""
    return self.repository + '@' + self.git_hash[:7]

  @property
  def id_string(self):
    """Returns a string that is unique to this repository and git hash."""
    return self.repository + '@' + self.git_hash

  @property
  def repository_url(self):
    """The HTTPS URL of the repository as passed to `git clone`."""
    repositories = namespaced_stored_object.Get(_REPOSITORIES_KEY)
    return repositories[self.repository]['repository_url']

  def Deps(self):
    """Return the DEPS of this Commit as a frozenset of Commits."""
    # Download and execute DEPS file.
    deps_file_contents = gitiles_service.FileContents(
        self.repository_url, self.git_hash, 'DEPS')
    deps_data = {'Var': lambda variable: deps_data['vars'][variable]}
    exec deps_file_contents in deps_data  # pylint: disable=exec-used

    # Pull out deps dict, including OS-specific deps.
    deps_dict = deps_data['deps']
    for deps_os in deps_data.get('deps_os', {}).itervalues():
      deps_dict.update(deps_os)

    # Convert deps strings to Commit objects.
    commits = []
    for dep_string in deps_dict.itervalues():
      dep_string_parts = dep_string.split('@')
      if len(dep_string_parts) < 2:
        continue  # Dep is not pinned to any particular revision.
      if len(dep_string_parts) > 2:
        raise NotImplementedError('Unknown DEP format: ' + dep_string)

      repository_url, git_hash = dep_string_parts
      try:
        repository = _Repository(repository_url)
      except KeyError:
        repository = _AddRepository(repository_url)
      commits.append(Commit(repository, git_hash))

    return frozenset(commits)

  def AsDict(self):
    return {
        'repository': self.repository,
        'git_hash': self.git_hash,
        'url': self.repository_url + '/+/' + self.git_hash,
    }

  @classmethod
  def FromDict(cls, data):
    """Create a Commit from a dict.

    If the repository is a repository URL, it will be translated to its short
    form name.

    Raises:
      KeyError: The repository name is not in the local datastore,
                or the git hash is not valid.
    """
    repository = data['repository']

    # Translate repository if it's a URL.
    if repository.startswith('https://'):
      repository = _Repository(repository)

    commit = cls(repository, data['git_hash'])

    try:
      gitiles_service.CommitInfo(commit.repository_url, commit.git_hash)
    except gitiles_service.NotFoundError as e:
      raise KeyError(str(e))

    return commit

  @classmethod
  def Midpoint(cls, commit_a, commit_b):
    """Return a Commit halfway between the two given Commits.

    Uses Gitiles to look up the commit range.

    Args:
      commit_a: The first Commit in the range.
      commit_b: The last Commit in the range.

    Returns:
      A new Commit representing the midpoint.
      The commit before the midpoint if the range has an even number of commits.
      commit_a if the Commits are the same or adjacent.

    Raises:
      NonLinearError: The Commits are in different repositories or commit_a does
        not come before commit_b.
    """
    if commit_a == commit_b:
      return commit_a

    if commit_a.repository != commit_b.repository:
      raise NonLinearError('Repositories differ between Commits: %s vs %s' %
                           (commit_a.repository, commit_b.repository))

    commits = gitiles_service.CommitRange(commit_a.repository_url,
                                          commit_a.git_hash, commit_b.git_hash)
    # We don't handle NotFoundErrors because we assume that all Commits either
    # came from this method or were already validated elsewhere.
    if len(commits) == 0:
      raise NonLinearError('Commit "%s" does not come before commit "%s".' %
                           commit_a, commit_b)
    if len(commits) == 1:
      return commit_a
    commits.pop(0)  # Remove commit_b from the range.

    return cls(commit_a.repository, commits[len(commits) / 2]['commit'])


def _Repository(repository_url):
  if repository_url.endswith('.git'):
    repository_url = repository_url[:-4]

  repositories = namespaced_stored_object.Get(_REPOSITORIES_KEY)
  for repo_label, repo_info in repositories.iteritems():
    if repository_url == repo_info['repository_url']:
      return repo_label

  raise KeyError('Unknown repository URL: ' + repository_url)


def _AddRepository(repository_url):
  if repository_url.endswith('.git'):
    repository_url = repository_url[:-4]

  repositories = namespaced_stored_object.Get(_REPOSITORIES_KEY)
  repository = repository_url.split('/')[-1]

  if repository in repositories:
    raise AssertionError("Attempted to add a repository that's already in the "
                         'Datastore: %s: %s' % (repository, repository_url))

  repositories[repository] = {'repository_url': repository_url}
  namespaced_stored_object.Set(_REPOSITORIES_KEY, repositories)

  return repository
