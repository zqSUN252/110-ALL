"""Bounded readers/writers for the existing format-1 JSON object of table arrays.

This is framing around Python's JSONDecoder, not a replacement JSON parser.
One row and the checkpoint metadata remain the minimum memory unit.
"""
import hashlib
import json


def file_hash(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def canonical_hash(value):
    result=hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=False).iterencode(value):result.update(part.encode('utf-8'))
    return result.hexdigest()


class ArrayHash:
    def __init__(self):self.value=hashlib.sha256(b'[');self.count=0
    def add(self,row):
        if self.count:self.value.update(b',')
        self.value.update(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8'));self.count+=1
    def hexdigest(self):
        value=self.value.copy();value.update(b']');return value.hexdigest()


class Reader:
    def __init__(self,stream,chunk_size):
        self.stream=stream;self.chunk_size=chunk_size;self.buffer='';self.position=0;self.eof=False;self.decoder=json.JSONDecoder()
    def more(self):
        self.buffer=self.buffer[self.position:];self.position=0
        chunk=self.stream.read(self.chunk_size);self.buffer+=chunk
        if not chunk:self.eof=True
    def peek(self):
        while True:
            while self.position<len(self.buffer) and self.buffer[self.position].isspace():self.position+=1
            if self.position<len(self.buffer):return self.buffer[self.position]
            if self.eof:return ''
            self.more()
    def expect(self,character):
        if self.peek()!=character:raise ValueError('Invalid backup JSON framing')
        self.position+=1
    def value(self):
        self.peek()
        while True:
            try:
                value,end=self.decoder.raw_decode(self.buffer,self.position);self.position=end;return value
            except json.JSONDecodeError:
                if self.eof:raise ValueError('Invalid or truncated backup JSON') from None
                self.more()
    def rows(self):
        if self.peek()!=']':
            while True:
                value=self.value()
                if not isinstance(value,dict):raise ValueError('Backup row must be an object')
                yield value
                if self.peek()==']':break
                self.expect(',')
        self.expect(']')


def tables(path,chunk_size=1024*1024):
    with path.open('r',encoding='utf-8') as stream:
        reader=Reader(stream,chunk_size);reader.expect('{');seen=set()
        if reader.peek()!='}':
            while True:
                name=reader.value()
                if not isinstance(name,str) or name in seen:raise ValueError('Invalid or duplicate backup table')
                seen.add(name);reader.expect(':');reader.expect('[')
                yield name,reader.rows()
                if reader.peek()=='}':break
                reader.expect(',')
        reader.expect('}')
        if reader.peek():raise ValueError('Trailing backup data')
