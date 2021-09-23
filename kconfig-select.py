#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#  Hacky helper for managing config files
#  for various Kconfig-based builds (Linux, Buildroot).
#
#  Note that this helper will just move/copy files around,
#  it does not offer automatic configuration and the like.
#
#
#  Usage:
#
#    kconfig-select list              -- list available config files
#    kconfig-select config [<name>]   -- copy <name> to build dir
#                                        (name defaults to "latest")
#    kconfig-select co [<name>]       -- alias to 'config'
#    kconfig-select backup [<name>]   -- copy build dir config
#                                        to <name> in config store dir
#                                        (name defaults to "config_<date>")
#                                        Automatic "-r<NUMBER>" suffix on conflict.
#    kconfig-select ci [<name>]       -- alias to 'backup'
#
#
# config store dir layout:
#
#    +- <config store dir>/
#       +- <build type>/
#          +- config_2021-08-01
#          +- config_2021-09-01-r1
#          +- latest => config_2021-09-01-r1
#             (symlink to most recently stored file)
#
# Changed files will be automatically committed
# if the config store directory is part of a git repository.
#
#
# Known build types:
#
#    * linux             [out-of-tree]
#    * buildroot         [out-of-tree]
#    * buildroot-busybox [out-of-tree]
#      (manages busybox.config in BUILDDIR)
#    * generic (config store subdir name usually derived from dirname)
#
#    Types mared with "[out-of-tree]" support initializing
#    a new build directory when a source directory ("-S", "--src") is given.
#
# If no build type is given ("-t", "--type" option) or "auto" is selected,
# the build type will be guessed in the order specified above.
#

import abc
import argparse
import collections
import datetime
import hashlib
import itertools
import operator
import os
import subprocess
import sys
import tempfile


def check_is_git_dir(dirpath):
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd    = dirpath,
        stdout = subprocess.DEVNULL,
        stderr = subprocess.DEVNULL,
    )

    return (proc.returncode == getattr(os, "EX_OK", 0))
# --- end of check_is_git_dir (...) ---


class BuildInfo(object):

    def __init__(self, src_dir, build_dir, config_root):
        super().__init__()
        self.src_dir     = src_dir
        self.build_dir   = build_dir
        self.config_root = config_root
    # --- end of __init__ (...) ---

# --- end of BuildInfo ---


class BuildType(object, metaclass=abc.ABCMeta):
    NAME = None
    ALIASES = []

    BUILD_CONFIG_FILENAME = ".config"

    def get_default_config_name(self):
        return "latest"
    # --- end of get_default_config_name (...) ---

    def get_default_backup_name(self):
        date_today = datetime.date.today()

        return date_today.strftime("config_%Y-%m-%d")
    # --- end of get_default_backup_name (...) ---

    def get_build_config_file_path(self):
        return os.path.join(self.info.build_dir, self.BUILD_CONFIG_FILENAME)
    # --- end of get_build_config_file_path (...) ---

    def __init__(self, info, ckey=None):
        super().__init__()
        self.info = info
        self.ckey = (ckey or self.NAME)

    def get_config_dir_path(self):
        if self.ckey:
            return os.path.join(self.info.config_root, self.ckey)
        else:
            return self.info.config_root
    # --- end of get_config_dir_path (...) ---

    @abc.abstractmethod
    def detect(self, guess_ckey=False):
        raise NotImplementedError(self)

    def prepare_src(self):
        pass

    def prepare_build(self):
        pass

    def prepare_build_outoftree(self):
        build_dir = self.info.build_dir

        if not os.path.isdir(build_dir):
            os.makedirs(build_dir, exist_ok=True)
            should_init = True

        elif not os.path.isfile(os.path.join(self.get_build_config_file_path())):
            # FIXME empty dir or missing config file, what is more appropriate?
            should_init = True

        else:
            should_init = False
        # --

        if should_init:
            subprocess.run(
                ["make", "-C", self.info.src_dir, f"O={build_dir}", "defconfig"],
                cwd   = self.info.src_dir,
                check = True,
            )
        # --
    # --- end of prepare_build_outoftree (...) ---

    def list_config_dir(self, config_dir):
        def scan_config_dir(config_dir):
            with os.scandir(config_dir) as dh:
                for entry in dh:
                    if entry.is_file(follow_symlinks=True):
                        yield entry
        # ---

        cmap = {
            e.name: e for e in scan_config_dir(config_dir)
            if (
                (e.name[0] != ".")
                and (not e.name.startswith("README"))
                and (not e.name.endswith(".tmp"))
            )
        }

        return cmap
    # --- end of list_config_dir (...) ---

    def get_config_dir(self, verbose=True):
        cdir = self.get_config_dir_path()

        try:
            cmap = self.list_config_dir(cdir)
        except FileNotFoundError:
            if verbose:
                sys.stderr.write(
                    "config store directory missing: {}\n".format(cdir)
                )
            cmap = None
        # --

        return (cdir, cmap)
    # --- end of get_config_dir (...) ---

    def copy_file(self, src, dst, *, bsize=2**14):
        sys.stdout.write(f"{src} -> {dst}\n")

        dst_dir = os.path.dirname(dst)

        (tmp_fd, tmp_filepath) = tempfile.mkstemp(suffix=".tmp", dir=dst_dir, text=False)
        try:
            with open(src, 'rb') as fh:
                block = fh.read(bsize)
                while block:
                    os.write(tmp_fd, block)
                    block = fh.read(bsize)
                # --
            # --

            os.close(tmp_fd)
            tmp_fd = None

            os.replace(tmp_filepath, dst)
            tmp_filepath = None

        finally:
            if tmp_fd is not None:
                os.close(tmp_fd)
                tmp_fd = None

            if tmp_filepath:
                os.unlink(tmp_filepath)
                tmp_filepath = None
            # --
        # --
    # --- end of copy_file (...) ---

    def run_list(self, long_format=False):
        if long_format:
            get_entry_str = operator.attrgetter('path')
        else:
            get_entry_str = operator.attrgetter('name')
        # --

        cdir, cmap = self.get_config_dir()
        if not cmap:
            return False

        for entry in sorted(cmap.values(), key=lambda e: e.name):
            print(get_entry_str(entry))
    # --- end of run_list (...) ---

    def run_config(self, name=None):
        if not name:
            name = self.get_default_config_name()

        cdir, cmap = self.get_config_dir()
        if not cmap:
            return False

        try:
            cfile = cmap[name]
        except KeyError:
            sys.stderr.write("config file not found: {}\n".format(name))
            return False

        bcfile = self.get_build_config_file_path()
        self.copy_file(cfile.path, bcfile)
    # --- end of run_config (...) ---

    def get_file_hash(self, filepath, *, bsize=2**14):
        hashobj = hashlib.new("sha256")

        with open(filepath, "rb") as fh:
            block = fh.read(bsize)
            while block:
                hashobj.update(block)
                block = fh.read(bsize)
        # --

        return hashobj.hexdigest()
    # --- end of get_file_hash (...) ---

    def run_backup(self, name=None):
        if not name:
            name = self.get_default_backup_name()

        bcfile = self.get_build_config_file_path()
        bcfile_hash = self.get_file_hash(bcfile)
        want_copy = False

        cdir, cmap = self.get_config_dir(verbose=False)

        if cmap is None:
            # cdir does not exist yet
            os.makedirs(cdir, exist_ok=True)
            want_copy = True

        elif name in cmap:
            def fgen(basename):
                yield basename

                for revno in itertools.count(1):
                    yield f"{basename}-r{revno}"
            # --- end of fgen (...) ---

            basename = name
            for name in fgen(basename):
                if name not in cmap:
                    want_copy = True
                    break

                else:
                    cfile_hash = self.get_file_hash(cmap[name].path)
                    if bcfile_hash == cfile_hash:
                        sys.stdout.write(f"File not changed: {name}\n")
                        want_copy = False
                        break
                # -- end if
            # -- end of infinite loop
        # -- end if

        cfile = os.path.join(cdir, name)
        cfile_link = os.path.join(cdir, self.get_default_config_name())

        if want_copy:
            git_changed = []

            self.copy_file(bcfile, cfile)
            git_changed.append(cfile)

            # FIXME FIXME FIXME hyper atomic
            try:
                os.unlink(cfile_link)
            except FileNotFoundError:
                pass

            os.symlink(os.path.basename(cfile), cfile_link)
            git_changed.append(cfile_link)

            if check_is_git_dir(cdir):
                self.run_git_commit_file(
                    cdir, git_changed, commit_text="update config"
                )
            # --
        # -- end if want_copy
    # --- end of run_backup (...) ---

    def run_git_commit_file(self, dirpath, filepaths, commit_text):
        subprocess.run(
            (["git", "add"] + filepaths),
            cwd   = dirpath,
            check = True,
        )

        subprocess.run(
            (["git", "commit", "-m", commit_text] + filepaths),
            cwd   = dirpath,
            check = True,
        )
    # --- end of run_git_commit_file (...) ---

# --- end of BuildType ---


class LinuxBuildType(BuildType):
    NAME = "linux"

    def detect(self, guess_ckey=False):
        return False

    def prepare_build(self):
        self.prepare_build_outoftree()

# --- end of LinuxBuildType ---


class BuildrootBuildType(BuildType):
    NAME = "buildroot"
    ALIASES = ["br"]

    def detect(self, guess_ckey=False):
        try:
            with open(os.path.join(self.info.src_dir, "Makefile"), "rt") as fh:
                first_line = fh.readline().strip()

        except FileNotFoundError:
            return False

        return (first_line == "# Makefile for buildroot")
    # --- end of detect (...) ---

    def prepare_build(self):
        self.prepare_build_outoftree()

# --- end of BuildrootBuildType ---


class BuildrootBusyboxBuildType(BuildrootBuildType):
    NAME = "buildroot-busybox"
    ALIASES = ["br-busybox", "brb"]

    BUILD_CONFIG_FILENAME = "busybox.config"
# --- end of BuildrootBusyboxBuildType ---


class GenericBuildType(BuildType):
    NAME = "generic"

    def detect(self, guess_ckey=False):
        if guess_ckey:
            self.ckey = os.path.basename(self.info.build_dir).strip(".").partition(".")[0]
            pass
        # --
        return True

# --- end of GenericBuildType ---


KNOWN_BUILD_TYPES = [
    LinuxBuildType,
    BuildrootBuildType,
    BuildrootBusyboxBuildType,   # must be listed after BuildrootBuildType
    GenericBuildType,
]


def get_build_type_map():
    bmap = collections.OrderedDict()
    bmap_alias = {}

    for btype_cls in KNOWN_BUILD_TYPES:
        if btype_cls.NAME:
            bmap[btype_cls.NAME] = btype_cls
            bmap_alias[btype_cls.NAME] = btype_cls

        if btype_cls.ALIASES:
            for btype_alias in btype_cls.ALIASES:
                bmap_alias[btype_alias] = btype_cls
    # --

    return (bmap, bmap_alias)
# --- end of get_build_type_map (...) ---


def main(prog, argv):
    (build_type_map, build_type_alias_map) = get_build_type_map()

    default_config_root = os.path.expanduser("~/git/kconfig-files")

    arg_parser = get_argument_parser(prog, default_config_root)
    arg_config = arg_parser.parse_args(argv)

    src_dir    = arg_config.src or os.getcwd()
    build_info = BuildInfo(
        src_dir     = os.path.abspath(src_dir),
        build_dir   = os.path.abspath(arg_config.build or src_dir),
        config_root = os.path.abspath(arg_config.config),
    )

    arg_type = arg_config.type
    if arg_type:
        arg_type = os.path.normpath(arg_type.lower().strip("/"))

    if not arg_type or arg_config == "auto":
        for btype_cls in build_type_map.values():
            build_type = btype_cls(build_info)

            if build_type.detect(guess_ckey=True):
                break

        else:
            raise NotImplementedError("no build type found for " + build_info.src_dir)

    elif arg_type in build_type_alias_map:
        build_type = build_type_alias_map[arg_type](build_info)

        if not build_type.detect():
            raise RuntimeError("build type mismatch", build_type.NAME)

    else:
        build_type = GenericBuildType(build_info, ckey=arg_type)

        if not build_type.detect(guess_ckey=True):
            raise RuntimeError("build type mismatch", build_type.NAME)
    # --

    if (
        (not getattr(arg_config, 'action', None))
        or (arg_config.action == "config")
    ):
        build_type.prepare_src()
        build_type.prepare_build()
        return build_type.run_config(getattr(arg_config, 'config_name', None))

    elif arg_config.action == "backup":
        return build_type.run_backup(getattr(arg_config, 'backup_name', None))

    elif arg_config.action == "list":
        return build_type.run_list(getattr(arg_config, 'long', None))

    else:
        raise NotImplementedError(arg_config.action)
# --- end of main (...) ---


def get_argument_parser(prog, default_config_root):
    prog_name = os.path.splitext(os.path.basename(prog))[0]

    arg_parser = argparse.ArgumentParser(
        prog=prog_name,
    )

    arg_parser.add_argument(
        "-S", "--src", metavar="<srcdir>",
        help="path to the sources directory (default: <cwd>)"
    )

    arg_parser.add_argument(
        "-O", "--build", metavar="<builddir>",
        help="path to the build directory (default: <srcdir>)"
    )

    arg_parser.add_argument(
        "-C", "--config", metavar="<configdir>",
        default=default_config_root,
        help="path to the config root directory (default: %(default)s)"
    )

    arg_parser.add_argument(
        "-t", "--type", metavar="<type>",
        help="sources type"
    )


    subparsers = arg_parser.add_subparsers(help="sub-commands")

    parser_list = subparsers.add_parser(
        "list", aliases=["l"],
        help="list available config files for this build",
    )
    parser_list.set_defaults(action="list")

    parser_list.add_argument(
        "-l", "--long",
        default=False, action="store_true",
        help="enable long output format"
    )

    parser_config = subparsers.add_parser(
        "config", aliases=["co"],
        help="copy config from config store to build"
    )
    parser_config.set_defaults(action="config")

    parser_config.add_argument(
        "config_name", nargs="?",
    )

    parser_backup = subparsers.add_parser(
        "backup", aliases=["ci"],
        help="copy config from build to config store"
    )
    parser_backup.set_defaults(action="backup")

    parser_backup.add_argument(
        "backup_name", nargs="?",
    )

    return arg_parser
# --- end of get_argument_parser (...) ---


if __name__ == '__main__':
    os_ex_ok = getattr(os, 'EX_OK', 0)

    try:
        exit_code = main(sys.argv[0], sys.argv[1:])

    except KeyboardInterrupt:
        exit_code = os_ex_ok ^ 130


    else:
        if (exit_code is True) or (exit_code is None):
            exit_code = os_ex_ok
        elif (exit_code is False):
            exit_code = os_ex_ok ^ 1
        else:
            pass
    # --

    sys.exit(exit_code)
# --
