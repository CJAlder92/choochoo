
from abc import abstractmethod, ABC
from inspect import getsource
from os import unlink, makedirs
from os.path import join, exists
from re import compile, sub
from sys import stdout

import nbformat.v4 as nbv
import nbformat as nb

from ..server import JupyterServer

QUOTES = "'''"
FQUOTES = 'f' + QUOTES
IPYNB = '.ipynb'


class Token(ABC):

    def __init__(self, vars):
        self._vars = vars

    # called when this token is complete
    def post_one(self):
        pass

    # called when all tokens are complete
    def post_all(self, tokens):
        pass

    @abstractmethod
    def to_cell(self):
        raise NotImplementedError()

    @staticmethod
    def to_notebook(tokens):
        for token in tokens:
            token.post_all(tokens)
        notebook = nbv.new_notebook()
        for token in tokens:
            notebook['cells'].append(token.to_cell())
        return notebook


class TextToken(Token):

    def __init__(self, vars, indent):
        super().__init__(vars)
        self._indent = indent
        self._text = ''

    def append(self, line):
        self._text += line[self._indent:]
        self._text += '\n'

    def __bool__(self):
        return bool(self._text)

    @staticmethod
    def _strip(text):
        while text.startswith('\n'):
            text = text[1:]
        while text.endswith('\n'):
            text = text[:-1]
        return text

    def __repr__(self):
        return f'{self.__class__.__name__}({QUOTES}\n{self._text}\n{QUOTES})'

    def to_cell(self):
        return nbv.new_markdown_cell(self._text)


class Text(TextToken):

    def __init__(self, vars, fmt, indent):
        super().__init__(vars, indent)
        self._fmt = fmt

    @staticmethod
    def _strip_right(text):
        # markdown in jupyter splits lines ending in multiple spaces.
        while text and text[-1] == ' ':
            text = text[:-1]
        return text

    def post_one(self):
        self._text = eval('%s%s%s' % (self._fmt, self._text, QUOTES), self._vars)
        self._text = '\n'.join([self._strip_right(line) for line in self._text.splitlines()])
        self._text = self._strip(self._text)

    def post_all(self, tokens):
        self._text = '\n'.join(self._expand_line(line, tokens) for line in self._text.splitlines())

    def _expand_line(self, line, tokens):
        if line == '$contents':
            return '## Contents\n' + self._contents(tokens)
        else:
            return line

    def _contents(self, tokens):
        sections = []
        for token in tokens:
            if isinstance(token, Text):
                for line in token._text.splitlines():
                    if line.startswith('## '):
                        title = line[3:].strip()
                        sections.append(f'* [{title}](#{title.replace(" ", "-")})')
        return '\n'.join(sections)

    @staticmethod
    def parse(vars, lines, f):
        text = Text(vars, f, 4)
        while lines and lines[0].strip() not in (QUOTES, FQUOTES):
            text.append(lines.pop(0))
        if text:
            text.post_one()
            yield text
        if lines:
            yield from Params.parse_text_or_code(vars, lines[1:])


class Code(TextToken):

    def post_one(self):
        self._text = self._strip(self._text)

    def to_cell(self):
        return nbv.new_code_cell(self._text)

    @staticmethod
    def parse(vars, lines):
        code = Code(vars, 4)
        while lines and lines[0].strip() not in (QUOTES, FQUOTES):
            code.append(lines.pop(0))
        if code:
            code.post_one()
            yield code
        if lines:
            yield from Params.parse_text_or_code(vars, lines)


class Import(Code):

    @staticmethod
    def parse(vars, lines):
        imports = Import(vars, 0)
        while lines:
            if lines[0].startswith('def '):
                if imports:
                    imports.post_one()
                    yield imports
                yield from Params.parse(vars, lines)
                return
            else:
                imports.append(lines.pop(0))


class Params(Code):

    def __init__(self, vars, params):
        super().__init__(vars, 0)
        self._params = [param.strip() for param in params]

    def post_one(self):
        for param in self._params:
            self.append(f'{param} = "{self._vars[param]}"')
        super().post_one()

    @staticmethod
    def parse(vars, lines):
        line = lines.pop(0)
        template = compile(r'def template\(([^)]*)\):\s*')
        match = template.match(line)
        if match.group(1):
            params = Params(vars, match.group(1).split(','))
            params.post_one()
            yield params
        elif not match:
            raise Exception(f'Bad template def: {line}')
        yield from Params.parse_text_or_code(vars, lines)

    @staticmethod
    def parse_text_or_code(vars, lines):
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines:
            if lines[0].strip() in (QUOTES, FQUOTES):
                yield from Text.parse(vars, lines[1:], lines[0].strip())
            else:
                yield from Code.parse(vars, lines)


def tokenize(vars, text):
    yield from Import.parse(vars, list(text.splitlines()))


def load_raw(name):
    return getsource(getattr(__import__('ch2.uranus.template', fromlist=[name]), name))


def load_tokens(name, **kargs):
    return tokenize(kargs, load_raw(name))


def load_notebook(name, **kargs):
    return Token.to_notebook(list(load_tokens(name, **kargs)))


def create_notebook(log, template, **kargs):
    args = ' '.join(str(kargs[key]) for key in sorted(kargs.keys()))
    args = sub(r'\s+', '-', args)
    name = args + IPYNB
    base = join(JupyterServer.singleton().notebook_dir, template)
    path = join(base, name)
    makedirs(base)
    log.info(f'Creating {template} with {kargs}')
    notebook = load_notebook(template, **kargs)
    if exists(path):
        log.warn(f'Deleting old version of {path}')
        unlink(path)
    with open(path, 'w') as out:
        log.info(f'Writing {template} to {path}')
        nb.write(notebook, out)
    return join(template, name)


if __name__ == '__main__':
    # print(tokens)
    notebook = load_notebook('compare_activities', activity_date='2018-03-01 16:00', compare_date='2017-09-19 16:00')
    nb.write(notebook, stdout)