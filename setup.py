from setuptools import setup, find_packages
from setuptools.command import easy_install

def install_script(self, dist, script_name, script_text, dev_path=None):
    script_text = easy_install.get_script_header(script_text) + (
        ''.join(script_text.splitlines(True)[1:]))

    self.write_script(script_name, script_text, 'b')

easy_install.easy_install.install_script = install_script

setup(
    name     = 'norless',
    version  = '0.1',
    author   = 'Anton Bobrov',
    author_email = 'bobrov@vl.ru',
    description = 'Yet another IMAP sync',
    # long_description = open('README.rst').read(),
    zip_safe   = False,
    data_files = [('norless',['norlessrc.example'])],
    packages = find_packages(exclude=('tests', 'tests.*')),
    include_package_data = True,
    scripts = ['bin/norless', 'bin/check-nl-mail'],
    url = 'http://github.com/baverman/norless',
    classifiers = [
        "Programming Language :: Python",
        "License :: OSI Approved :: MIT License",
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "Natural Language :: English",
    ],
)
