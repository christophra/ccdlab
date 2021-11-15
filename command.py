from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys
import shlex
from copy import deepcopy

class Command:
    """1) Parse a text command into command name and arguments, both positional and keyword.
    2) Compose a command name and its arguments into a string.

    The expected format is
        name arg1 arg2 kwarg1=value1 kwarg2=value2
    where keyword arguments can stand at any position except the first.
    Each component (name, args, kwargs) is optional.
    The parsing output stored as
        self.name = name, or None if not provided
        self.kwarg = {key:value}, or {} if not provided
        self.args = [arg], or [] if not provided
    """
    # TODO: add @property
    # TODO: add way to store nbytes? or at least split arg in (value, nbytes)?
    def __init__(self, arg):
        if type(arg) == str:
            # parse into data structure
            self._construct_parse(arg)
        elif type(arg) == Command:
            # compose back to string
            self._construct_compose(arg)
        else:
            raise TypeError('Can not construct from object of type {}'.format(type(arg)))

    def _construct_parse(self, string):
        self.name = None
        self.string = None
        self.body = None
        self.args = []
        self.kwargs = {}
        self.chunks = [] # Raw split chunks
        self.parse(string)

    def _construct_compose(self, command):
        for attribute in ['name', 'string', 'body', 'args', 'kwargs', 'chunks']:
            self.__dict__[attribute] = deepcopy(command.__getattribute__(attribute))
        self.compose()

    def get(self, key, value=None):
        return self.kwargs.get(key, value)

    def has_key(self, key):
        return key in self.kwargs

    def __contains__(self, key):
        return key in self.kwargs

    def parse(self, string):
        self.string = string
        self.body = string
        self.chunks = shlex.split(string)

        for i,chunk in enumerate(self.chunks):
            if '=' not in chunk:
                # first chunk: must be the command name
                if i == 0:
                    self.name = chunk
                    self.body = self.string.strip()[len(chunk):].strip()
                # any other position: is a positional argument
                else:
                    self.args.append(chunk)
            else:
                pos = chunk.find('=')
                self.kwargs[chunk[:pos]] = chunk[pos+1:]

    def compose(self):
        # collect chunks of the body
        chunks = []
        for _arg in self.args:
            chunks.append(_arg)
        for _kwarg,_value in self.kwargs.items():
            chunks.append('{_kwarg}={_value}'.format(**locals()))

        # join into body string, empty if no args and kwargs
        body = ' '.join(chunks)

        # if no name, the string is just the body
        if self.name is None:
            string = body
        # else, the name goes first
        else:
            string = ' '.join([self.name, body])

        # set body attribute
        if body:
            self.body = body
        # default is None (if neither args nor kwargs)
        else:
            self.body = None

        # set string attribute
        if string:
            self.string = string
        # default is None (if neither name nor body)
        else:
            self.string = None




def sanitize_command_line(input):
    """Sanitize a string e.g. sent over the network so it can be parsed as a command.
    """
    output = input

    # strip line endings, which may be LF or CR LF
    output = input.strip('\n').strip('\r')

    return output
