"""Validated HTML data for the observed native TinyMCE editor, never code."""
from html.parser import HTMLParser
from urllib.parse import urlsplit


class Content(HTMLParser):
    tags=set('p br strong b em i u s ul ol li h1 h2 h3 h4 h5 h6 table thead tbody tfoot tr td th caption blockquote a span div hr sub sup pre code'.split())
    attrs=set('href title class style colspan rowspan scope target rel start'.split())
    css=set('color background-color text-align font-weight font-style text-decoration font-size font-family margin-left padding-left border border-collapse width height vertical-align'.split())
    def __init__(self):
        super().__init__(convert_charrefs=True);self.tokens=[]
    def handle_starttag(self,tag,attrs):
        if tag not in self.tags:raise ValueError('Rich text contains an unsupported element; use formatted text without active content.')
        for key,value in attrs:
            value=value or ''
            if key not in self.attrs:raise ValueError('Rich text contains an unsupported attribute.')
            if key=='href' and (any(ord(c)<32 for c in value) or urlsplit(value).scheme.lower() not in {'','https','http','mailto'}):
                raise ValueError('Rich text links must use ordinary web or email URLs.')
            if key=='style':
                for declaration in value.split(';'):
                    if not declaration.strip():continue
                    prop,sep,setting=declaration.partition(':')
                    if not sep or prop.strip().lower() not in self.css or any(c in setting for c in '(){}\\@'):
                        raise ValueError('Rich text style is not a supported formatting property.')
        self.tokens.append(('start',tag,tuple(sorted(attrs))))
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs)
    def handle_endtag(self,tag):
        if tag not in self.tags:raise ValueError('Unsupported rich text closing element.')
        if tag not in {'br','hr'}:self.tokens.append(('end',tag))
    def handle_data(self,data):
        if data.strip():self.tokens.append(('text',data))
    def handle_decl(self,decl):raise ValueError('Rich text declarations are unsupported.')


def canonical(value):
    parser=Content();parser.feed(value);parser.close();return parser.tokens


def apply(page,key,value):
    canonical(value)
    page.evaluate("""({key,value})=>{
      const editor=window.tinymce?.get(key);
      if(!editor||editor.mode?.get()==='readonly')throw Error('The observed rich text editor is unavailable.');
      editor.setContent(value,{format:'html'});editor.save();
    }""",{'key':key,'value':value})
