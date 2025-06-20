# Copyright (c) 2009, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Willow Garage, Inc. nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# Author Tully Foote/tfoote@willowgarage.com

import os
import subprocess
import sys

from configparser import ConfigParser
from pathlib import Path

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata

from ..core import InstallFailed
from ..installers import PackageManagerInstaller
from ..shell_utils import read_stdout

# pip package manager key
PIP_INSTALLER = 'pip'

EXTERNALLY_MANAGED_EXPLAINER = """
rosdep installation of pip packages requires installing packages globally as root.
When using Python >= 3.11, PEP 668 compliance requires you to allow pip to install alongside
externally managed packages using the 'break-system-packages' option.
The recommeded way to set this option when using rosdep is to set the environment variable
PIP_BREAK_SYSTEM_PACKAGES=1
in your environment.

For more information refer to http://docs.ros.org/en/independent/api/rosdep/html/pip_and_pep_668.html
"""


def register_installers(context):
    context.set_installer(PIP_INSTALLER, PipInstaller())


def get_pip_command():
    # First try pip2 or pip3
    cmd = ['pip' + os.environ['ROS_PYTHON_VERSION']]
    if is_cmd_available(cmd):
        return cmd

    # Second, try using the same python executable since we know that exists
    if os.environ['ROS_PYTHON_VERSION'] == sys.version[0]:
        try:
            import pip
        except ImportError:
            pass
        else:
            return [sys.executable, '-m', 'pip']

    # Finally, try python2 or python3 commands
    cmd = ['python' + os.environ['ROS_PYTHON_VERSION'], '-m', 'pip']
    if is_cmd_available(cmd):
        return cmd
    return None


def externally_managed_installable():
    """
    PEP 668 enacted in Python 3.11 blocks pip from working in "externally
    managed" environments such as operating systems with included package
    managers. If we're on Python 3.11 or greater, we need to check that pip
    is configured to allow installing system-wide packages with the
    flagrantly named "break system packages" config option or environment
    variable.
    """

    # This doesn't affect Python versions before 3.11
    if sys.version_info < (3, 11):
        return True

    if (
            'PIP_BREAK_SYSTEM_PACKAGES' in os.environ and
            os.environ['PIP_BREAK_SYSTEM_PACKAGES'].lower() in ('yes', '1', 'true')
    ):
        return True

    # Check the same configuration directories as pip does per
    # https://pip.pypa.io/en/stable/topics/configuration/
    pip_config = ConfigParser()
    if 'XDG_CONFIG_DIRS' in os.environ:
        for xdg_dir in os.environ['XDG_CONFIG_DIRS'].split(':'):
            pip_config_file = Path(xdg_dir) / 'pip' / 'pip.conf'
            pip_config.read(pip_config_file)
            if pip_config.getboolean('install', 'break-system-packages', fallback=False):
                return True

    fallback_config = Path('/etc/pip.conf')
    pip_config.read(fallback_config)
    if pip_config.getboolean('install', 'break-system-packages', fallback=False):
        return True
    # On Python 3.11 and later, when no explicit configuration is present,
    # global pip installation will not work.
    return False


def is_cmd_available(cmd):
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _ = proc.communicate()
        return 0 == proc.returncode
    except OSError:
        return False


def pip_detect(pkgs, exec_fn=None):
    """
    Given a list of package, return the list of installed packages.

    :param exec_fn: function to execute Popen and read stdout (for testing)
    """
    pip_cmd = get_pip_command()
    if not pip_cmd:
        return []

    fallback_to_pip_show = False
    if exec_fn is None:
        exec_fn = read_stdout
        fallback_to_pip_show = True
    pkg_list = exec_fn(pip_cmd + ['freeze']).split('\n')

    ret_list = []
    for pkg in pkg_list:
        pkg_row = pkg.split('==')
        if pkg_row[0] in pkgs:
            ret_list.append(pkg_row[0])

    # Try to detect with the return code of `pip show`.
    # This can show the existance of things like `argparse` which
    # otherwise do not show up.
    # See:
    #   https://github.com/pypa/pip/issues/1570#issuecomment-71111030
    if fallback_to_pip_show:
        for pkg in [p for p in pkgs if p not in ret_list]:
            # does not see retcode but stdout for old pip to check if installed
            proc = subprocess.Popen(
                pip_cmd + ['show', pkg.split('@')[0].strip()],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            output, _ = proc.communicate()
            output = output.strip()
            if proc.returncode == 0 and output:
                # `pip show` detected it, add it to the list.
                ret_list.append(pkg)

    return ret_list


class PipInstaller(PackageManagerInstaller):
    """
    :class:`Installer` support for pip.
    """

    def __init__(self):
        super(PipInstaller, self).__init__(pip_detect, supports_depends=True)

        # Pass necessary environment for pip functionality via sudo
        if self.as_root and self.sudo_command != '':
            self.sudo_command += ' --preserve-env=PIP_BREAK_SYSTEM_PACKAGES'

    def get_version_strings(self):
        pip_version = importlib_metadata.version('pip')
        # keeping the name "setuptools" for backward compatibility
        setuptools_version = importlib_metadata.version('setuptools')
        version_strings = [
            'pip {}'.format(pip_version),
            'setuptools {}'.format(setuptools_version),
        ]
        return version_strings

    def get_install_command(self, resolved, interactive=True, reinstall=False, quiet=False):
        pip_cmd = get_pip_command()
        if not pip_cmd:
            raise InstallFailed((PIP_INSTALLER, 'pip is not installed'))
        if not externally_managed_installable():
            raise InstallFailed((PIP_INSTALLER, EXTERNALLY_MANAGED_EXPLAINER))
        packages = self.get_packages_to_install(resolved, reinstall=reinstall)
        if not packages:
            return []
        cmd = pip_cmd + ['install', '-U']
        if quiet:
            cmd.append('-q')
        if reinstall:
            cmd.append('-I')
        return [self.elevate_priv(cmd + [p]) for p in packages]
