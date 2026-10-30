function e(e,t,s,n){Object.defineProperty(e,t,{get:s,set:n,enumerable:!0,configurable:!0})}var t,s,n,r,a,i,o,l,u,c,h,p,d,m,f,g,b,k,v,w,y={},C=[],S=/acit|ex(?:s|g|n|p|$)|rph|grid|ows|mnc|ntw|ine[ch]|zoo|^ord|itera/i,E=Array.isArray;function $(e,t){for(var s in t)e[s]=t[s];return e}function _(e){e&&e.parentNode&&e.parentNode.removeChild(e)}function I(e,t,s){var n,r,a,o={};for(a in t)"key"==a?n=t[a]:"ref"==a?r=t[a]:o[a]=t[a];if(arguments.length>2&&(o.children=arguments.length>3?i.call(arguments,2):s),"function"==typeof e&&null!=e.defaultProps)for(a in e.defaultProps)void 0===o[a]&&(o[a]=e.defaultProps[a]);return N(e,o,n,r,null)}function N(e,t,s,n,r){var a={type:e,props:t,key:s,ref:n,__k:null,__:null,__b:0,__e:null,__c:null,constructor:void 0,__v:null==r?++l:r,__i:-1,__u:0};return null==r&&null!=o.vnode&&o.vnode(a),a}function x(){return{current:null}}function T(e){return e.children}function R(e,t){this.props=e,this.context=t}function D(e,t){if(null==t)return e.__?D(e.__,e.__i+1):null;for(var s;t<e.__k.length;t++)if(null!=(s=e.__k[t])&&null!=s.__e)return s.__e;return"function"==typeof e.type?D(e):null}function O(e){(!e.__d&&(e.__d=!0)&&u.push(e)&&!A.__r++||c!=o.debounceRendering)&&((c=o.debounceRendering)||h)(A)}function A(){try{for(var e,t=1;u.length;)u.length>t&&u.sort(p),e=u.shift(),t=u.length,function(e){if(e.__P&&e.__d){var t=e.__v,s=t.__e,n=[],r=[],a=$({},t);a.__v=t.__v+1,o.vnode&&o.vnode(a),H(e.__P,a,t,e.__n,e.__P.namespaceURI,32&t.__u?[s]:null,n,null==s?D(t):s,!!(32&t.__u),r),a.__v=t.__v,a.__.__k[a.__i]=a,j(n,a,r),t.__e=t.__=null,a.__e!=s&&function e(t){if(null!=(t=t.__)&&null!=t.__c)return t.__e=t.__c.base=null,t.__k.some(function(e){if(null!=e&&null!=e.__e)return t.__e=t.__c.base=e.__e}),e(t)}(a)}}(e)}finally{u.length=A.__r=0}}function L(e,t,s,n,r,a,i,l,u,c,h){var p,d,m,f,g,b,k=n&&n.__k||C,v=t.length;for(u=function(e,t,s,n,r){var a,i,l,u,c,h=s.length,p=h,d=0;for(e.__k=Array(r),a=0;a<r;a++)null!=(i=t[a])&&"boolean"!=typeof i&&"function"!=typeof i?("string"==typeof i||"number"==typeof i||"bigint"==typeof i||i.constructor==String?i=e.__k[a]=N(null,i,null,null,null):E(i)?i=e.__k[a]=N(T,{children:i},null,null,null):void 0===i.constructor&&i.__b>0?i=e.__k[a]=N(i.type,i.props,i.key,i.ref?i.ref:null,i.__v):e.__k[a]=i,u=a+d,i.__=e,i.__b=e.__b+1,l=null,-1!=(c=i.__i=function(e,t,s,n){var r,a,i,o=e.key,l=e.type,u=t[s],c=null!=u&&0==(2&u.__u);if(null===u&&null==o||c&&o==u.key&&l==u.type)return s;if(n>+!!c){for(r=s-1,a=s+1;r>=0||a<t.length;)if(null!=(u=t[i=r>=0?r--:a++])&&0==(2&u.__u)&&o==u.key&&l==u.type)return i}return -1}(i,s,u,p))&&(p--,(l=s[c])&&(l.__u|=2)),null==l||null==l.__v?(-1==c&&(r>h?d--:r<h&&d++),"function"!=typeof i.type&&(i.__u|=4)):c!=u&&(c==u-1?d--:c==u+1?d++:(c>u?d--:d++,i.__u|=4))):e.__k[a]=null;if(p)for(a=0;a<h;a++)null!=(l=s[a])&&0==(2&l.__u)&&(l.__e==n&&(n=D(l)),function e(t,s,n){var r,a;if(o.unmount&&o.unmount(t),(r=t.ref)&&(r.current&&r.current!=t.__e||U(r,null,s)),null!=(r=t.__c)){if(r.componentWillUnmount)try{r.componentWillUnmount()}catch(e){o.__e(e,s)}r.base=r.__P=r.__n=null}if(r=t.__k)for(a=0;a<r.length;a++)r[a]&&e(r[a],s,n||"function"!=typeof t.type);n||_(t.__e),t.__c=t.__=t.__e=void 0}(l,l));return n}(s,t,k,u,v),p=0;p<v;p++)null!=(m=s.__k[p])&&(d=-1!=m.__i&&k[m.__i]||y,m.__i=p,b=H(e,m,d,r,a,i,l,u,c,h),f=m.__e,m.ref&&d.ref!=m.ref&&(d.ref&&U(d.ref,null,m),h.push(m.ref,m.__c||f,m)),null==g&&null!=f&&(g=f),4&m.__u?(u=function e(t,s,n){var r,a;if("function"==typeof t.type){for(r=t.__k,a=0;r&&a<r.length;a++)r[a]&&(r[a].__=t,s=e(r[a],s,n));return s}t.__e!=s&&(s&&t.type&&!s.parentNode&&(s=D(t)),s=n.insertBefore(t.__e,s||null));do s=s&&s.nextSibling;while(null!=s&&8==s.nodeType)return s}(m,u,e),d.__e&&(d.__e=null)):"function"==typeof m.type&&void 0!==b?u=b:f&&(u=f.nextSibling),m.__u&=-7);return s.__e=g,u}function M(e,t,s){"-"==t[0]?e.setProperty(t,null==s?"":s):e[t]=null==s?"":"number"!=typeof s||S.test(t)?s:s+"px"}function P(e,t,s,n,r){var a,i;e:if("style"==t)if("string"==typeof s)e.style.cssText=s;else{if("string"==typeof n&&(e.style.cssText=n=""),n)for(t in n)s&&t in s||M(e.style,t,"");if(s)for(t in s)n&&s[t]==n[t]||M(e.style,t,s[t])}else if("o"==t[0]&&"n"==t[1])a=t!=(t=t.replace(g,"$1")),t=(i=t.toLowerCase())in e||"onFocusOut"==t||"onFocusIn"==t?i.slice(2):t.slice(2),e.l||(e.l={}),e.l[t+a]=s,s?n?s[f]=n[f]:(s[f]=b,e.addEventListener(t,a?v:k,a)):e.removeEventListener(t,a?v:k,a);else{if("http://www.w3.org/2000/svg"==r)t=t.replace(/xlink(H|:h)/,"h").replace(/sName$/,"s");else if("width"!=t&&"height"!=t&&"href"!=t&&"list"!=t&&"form"!=t&&"tabIndex"!=t&&"download"!=t&&"rowSpan"!=t&&"colSpan"!=t&&"role"!=t&&"popover"!=t&&t in e)try{e[t]=null==s?"":s;break e}catch(e){}"function"==typeof s||(null==s||!1===s&&"-"!=t[4]?e.removeAttribute(t):e.setAttribute(t,"popover"==t&&1==s?"":s))}}function B(e){return function(t){if(this.l){var s=this.l[t.type+e];if(null==t[m])t[m]=b++;else if(t[m]<s[f])return;return s(o.event?o.event(t):t)}}}function H(e,t,s,n,r,a,l,u,c,h){var p,d,m,f,g,b,k,v,w,S,I,N,x,O,A,M,B=t.type;if(void 0!==t.constructor)return null;128&s.__u&&(c=!!(32&s.__u),a=[u=t.__e=s.__e]),(p=o.__b)&&p(t);e:if("function"==typeof B){d=l.length;try{if(w=t.props,S=B.prototype&&B.prototype.render,I=(p=B.contextType)&&n[p.__c],N=p?I?I.props.value:p.__:n,s.__c?v=(m=t.__c=s.__c).__=m.__E:(S?t.__c=m=new B(w,N):(t.__c=m=new R(w,N),m.constructor=B,m.render=W),I&&I.sub(m),m.state||(m.state={}),m.__n=n,f=m.__d=!0,m.__h=[],m._sb=[]),S&&null==m.__s&&(m.__s=m.state),S&&null!=B.getDerivedStateFromProps&&(m.__s==m.state&&(m.__s=$({},m.__s)),$(m.__s,B.getDerivedStateFromProps(w,m.__s))),g=m.props,b=m.state,m.__v=t,f)S&&null==B.getDerivedStateFromProps&&null!=m.componentWillMount&&m.componentWillMount(),S&&null!=m.componentDidMount&&m.__h.push(m.componentDidMount);else{if(S&&null==B.getDerivedStateFromProps&&w!==g&&null!=m.componentWillReceiveProps&&m.componentWillReceiveProps(w,N),t.__v==s.__v||!m.__e&&null!=m.shouldComponentUpdate&&!1===m.shouldComponentUpdate(w,m.__s,N)){t.__v!=s.__v&&(m.props=w,m.state=m.__s,m.__d=!1),t.__e=s.__e,t.__k=s.__k,t.__k.some(function(e){e&&(e.__=t)}),C.push.apply(m.__h,m._sb),m._sb=[],m.__h.length&&l.push(m),u=D(s);break e}null!=m.componentWillUpdate&&m.componentWillUpdate(w,m.__s,N),S&&null!=m.componentDidUpdate&&m.__h.push(function(){m.componentDidUpdate(g,b,k)})}if(m.context=N,m.props=w,m.__P=e,m.__e=!1,x=o.__r,O=0,S)m.state=m.__s,m.__d=!1,x&&x(t),p=m.render(m.props,m.state,m.context),C.push.apply(m.__h,m._sb),m._sb=[];else do m.__d=!1,x&&x(t),p=m.render(m.props,m.state,m.context),m.state=m.__s;while(m.__d&&++O<25)m.state=m.__s,null!=m.getChildContext&&(n=$($({},n),m.getChildContext())),S&&!f&&null!=m.getSnapshotBeforeUpdate&&(k=m.getSnapshotBeforeUpdate(g,b)),A=null!=p&&p.type===T&&null==p.key?function e(t){return"object"!=typeof t||null==t||t.__b>0?t:E(t)?t.map(e):void 0!==t.constructor?null:$({},t)}(p.props.children):p,u=L(e,E(A)?A:[A],t,s,n,r,a,l,u,c,h),m.base=t.__e,t.__u&=-161,m.__h.length&&l.push(m),v&&(m.__E=m.__=null)}catch(e){if(l.length=d,t.__v=null,c||null!=a){if(e.then){for(t.__u|=c?160:128;u&&8==u.nodeType&&u.nextSibling;)u=u.nextSibling;null!=a&&(a[a.indexOf(u)]=null),t.__e=u}else if(null!=a)for(M=a.length;M--;)_(a[M])}else t.__e=s.__e;null==t.__k&&(t.__k=s.__k||[]),e.then||function e(t){t&&(t.__c&&(t.__c.__e=!0),t.__k&&t.__k.some(e))}(t),o.__e(e,t,s)}}else null==a&&t.__v==s.__v?(t.__k=s.__k,t.__e=s.__e):u=t.__e=function(e,t,s,n,r,a,l,u,c){var h,p,d,m,f,g,b,k=s.props||y,v=t.props,w=t.type;if("svg"==w?r="http://www.w3.org/2000/svg":"math"==w?r="http://www.w3.org/1998/Math/MathML":r||(r="http://www.w3.org/1999/xhtml"),null!=a){for(h=0;h<a.length;h++)if((f=a[h])&&"setAttribute"in f==!!w&&(w?f.localName==w:3==f.nodeType)){e=f,a[h]=null;break}}if(null==e){if(null==w)return document.createTextNode(v);e=document.createElementNS(r,w,v.is&&v),u&&(o.__m&&o.__m(t,a),u=!1),a=null}if(null==w)k===v||u&&e.data==v||(e.data=v);else{if(a="textarea"==w&&null!=v.defaultValue?null:a&&i.call(e.childNodes),!u&&null!=a)for(k={},h=0;h<e.attributes.length;h++)k[(f=e.attributes[h]).name]=f.value;for(h in k)f=k[h],"dangerouslySetInnerHTML"==h?d=f:"children"==h||h in v||"value"==h&&"defaultValue"in v||"checked"==h&&"defaultChecked"in v||P(e,h,null,f,r);for(h in v)f=v[h],"children"==h?m=f:"dangerouslySetInnerHTML"==h?p=f:"value"==h?g=f:"checked"==h?b=f:u&&"function"!=typeof f||k[h]===f||P(e,h,f,k[h],r);if(p)u||d&&(p.__html==d.__html||p.__html==e.innerHTML)||(e.innerHTML=p.__html),t.__k=[];else if(d&&(e.innerHTML=""),L("template"==t.type?e.content:e,E(m)?m:[m],t,s,n,"foreignObject"==w?"http://www.w3.org/1999/xhtml":r,a,l,a?a[0]:s.__k&&D(s,0),u,c),null!=a)for(h=a.length;h--;)_(a[h]);u&&"textarea"!=w||(h="value","progress"==w&&null==g?e.removeAttribute("value"):null==g||g===e[h]&&("progress"!=w||g)&&("option"!=w||g==k[h])||P(e,h,g,k[h],r),h="checked",null!=b&&b!=e[h]&&P(e,h,b,k[h],r))}return e}(s.__e,t,s,n,r,a,l,c,h);return(p=o.diffed)&&p(t),128&t.__u?void 0:u}function j(e,t,s){for(var n=0;n<s.length;n++)U(s[n],s[++n],s[++n]);o.__c&&o.__c(t,e),e.some(function(t){try{e=t.__h,t.__h=[],e.some(function(e){e.call(t)})}catch(e){o.__e(e,t.__v)}})}function U(e,t,s){try{if("function"==typeof e){var n="function"==typeof e.__u;n&&e.__u(),n&&null==t||(e.__u=e(t))}else e.current=t}catch(e){o.__e(e,s)}}function W(e,t,s){return this.constructor(e,s)}i=C.slice,o={__e:function(e,t,s,n){for(var r,a,i;t=t.__;)if((r=t.__c)&&!r.__)try{if((a=r.constructor)&&null!=a.getDerivedStateFromError&&(r.setState(a.getDerivedStateFromError(e)),i=r.__d),null!=r.componentDidCatch&&(r.componentDidCatch(e,n||{}),i=r.__d),i)return r.__E=r}catch(t){e=t}throw e}},l=0,R.prototype.setState=function(e,t){var s;s=null!=this.__s&&this.__s!=this.state?this.__s:this.__s=$({},this.state),"function"==typeof e&&(e=e($({},s),this.props)),e&&$(s,e),null!=e&&this.__v&&(t&&this._sb.push(t),O(this))},R.prototype.forceUpdate=function(e){this.__v&&(this.__e=!0,e&&this.__h.push(e),O(this))},R.prototype.render=T,u=[],h="function"==typeof Promise?Promise.prototype.then.bind(Promise.resolve()):setTimeout,p=function(e,t){return e.__v.__b-t.__v.__b},A.__r=0,m="__d"+(d=Math.random().toString(8)),f="__a"+d,g=/(PointerCapture)$|Capture$/i,b=0,k=B(!1),v=B(!0),w=0;var F=function(e,t,s,n){var r;t[0]=0;for(var a=1;a<t.length;a++){var i=t[a++],o=t[a]?(t[0]|=i?1:2,s[t[a++]]):t[++a];3===i?n[0]=o:4===i?n[1]=Object.assign(n[1]||{},o):5===i?(n[1]=n[1]||{})[t[++a]]=o:6===i?n[1][t[++a]]+=o+"":i?(r=e.apply(o,F(e,o,s,["",null])),n.push(r),o[0]?t[0]|=2:(t[a-2]=0,t[a]=r)):n.push(o)}return n},K=new Map,G={};e(G,"State",()=>et),e(G,"regexp",()=>e6),e(G,"stringToArray",()=>te),e(G,"Options",()=>tr),e(G,"options",()=>ti),e(G,"MultiToken",()=>to),e(G,"createTokenClass",()=>tl),e(G,"multi",()=>td),e(G,"reset",()=>tv),e(G,"registerTokenPlugin",()=>tw),e(G,"registerPlugin",()=>ty),e(G,"registerCustomProtocol",()=>tC),e(G,"init",()=>tS),e(G,"tokenize",()=>tE),e(G,"find",()=>t$),e(G,"test",()=>t_),e(G,"text",()=>td);let z="numeric",q="ascii",V="alpha",Q="asciinumeric",J="alphanumeric",Y="domain",X="emoji",Z="whitespace";function ee(e,t,s){for(let n in t[z]&&(t[Q]=!0,t[J]=!0),t[q]&&(t[Q]=!0,t[V]=!0),t[Q]&&(t[J]=!0),t[V]&&(t[J]=!0),t[J]&&(t[Y]=!0),t[X]&&(t[Y]=!0),t){let t=(n in s||(s[n]=[]),s[n]);0>t.indexOf(e)&&t.push(e)}}function et(e=null){this.j={},this.jr=[],this.jd=null,this.t=e}et.groups={},et.prototype={accepts(){return!!this.t},go(e){let t=this.j[e];if(t)return t;for(let t=0;t<this.jr.length;t++){let s=this.jr[t][0],n=this.jr[t][1];if(n&&s.test(e))return n}return this.jd},has(e,t=!1){return t?e in this.j:!!this.go(e)},ta(e,t,s,n){for(let r=0;r<e.length;r++)this.tt(e[r],t,s,n)},tr(e,t,s,n){let r;return n=n||et.groups,t&&t.j?r=t:(r=new et(t),s&&n&&ee(t,s,n)),this.jr.push([e,r]),r},ts(e,t,s,n){let r=this,a=e.length;if(!a)return r;for(let t=0;t<a-1;t++)r=r.tt(e[t]);return r.tt(e[a-1],t,s,n)},tt(e,t,s,n){if(n=n||et.groups,t&&t.j)return this.j[e]=t,t;let r,a=this.go(e);return a?(Object.assign((r=new et).j,a.j),r.jr.push.apply(r.jr,a.jr),r.jd=a.jd,r.t=a.t):r=new et,t&&(n&&(r.t&&"string"==typeof r.t?ee(t,Object.assign(function(e,t){let s={};for(let n in t)t[n].indexOf(e)>=0&&(s[n]=!0);return s}(r.t,n),s),n):s&&ee(t,s,n)),r.t=t),this.j[e]=r,r}};let es=(e,t,s,n,r)=>e.ta(t,s,n,r),en=(e,t,s,n,r)=>e.tr(t,s,n,r),er=(e,t,s,n,r)=>e.ts(t,s,n,r),ea=(e,t,s,n,r)=>e.tt(t,s,n,r),ei="WORD",eo="UWORD",el="ASCIINUMERICAL",eu="ALPHANUMERICAL",ec="LOCALHOST",eh="UTLD",ep="SCHEME",ed="SLASH_SCHEME",em="OPENBRACE",ef="CLOSEBRACE",eg="OPENBRACKET",eb="CLOSEBRACKET",ek="OPENPAREN",ev="CLOSEPAREN",ew="OPENANGLEBRACKET",ey="CLOSEANGLEBRACKET",eC="FULLWIDTHLEFTPAREN",eS="FULLWIDTHRIGHTPAREN",eE="LEFTCORNERBRACKET",e$="RIGHTCORNERBRACKET",e_="LEFTWHITECORNERBRACKET",eI="RIGHTWHITECORNERBRACKET",eN="FULLWIDTHLESSTHAN",ex="FULLWIDTHGREATERTHAN",eT="AMPERSAND",eR="APOSTROPHE",eD="ASTERISK",eO="BACKSLASH",eA="BACKTICK",eL="CARET",eM="COLON",eP="COMMA",eB="DOLLAR",eH="EQUALS",ej="EXCLAMATION",eU="HYPHEN",eW="PERCENT",eF="PIPE",eK="PLUS",eG="POUND",ez="QUERY",eq="QUOTE",eV="FULLWIDTHMIDDLEDOT",eQ="SEMI",eJ="SLASH",eY="TILDE",eX="UNDERSCORE",eZ="EMOJI";var e0=Object.freeze({__proto__:null,ALPHANUMERICAL:eu,AMPERSAND:eT,APOSTROPHE:eR,ASCIINUMERICAL:el,ASTERISK:eD,AT:"AT",BACKSLASH:eO,BACKTICK:eA,CARET:eL,CLOSEANGLEBRACKET:ey,CLOSEBRACE:ef,CLOSEBRACKET:eb,CLOSEPAREN:ev,COLON:eM,COMMA:eP,DOLLAR:eB,DOT:"DOT",EMOJI:eZ,EQUALS:eH,EXCLAMATION:ej,FULLWIDTHGREATERTHAN:ex,FULLWIDTHLEFTPAREN:eC,FULLWIDTHLESSTHAN:eN,FULLWIDTHMIDDLEDOT:eV,FULLWIDTHRIGHTPAREN:eS,HYPHEN:eU,LEFTCORNERBRACKET:eE,LEFTWHITECORNERBRACKET:e_,LOCALHOST:ec,NL:"NL",NUM:"NUM",OPENANGLEBRACKET:ew,OPENBRACE:em,OPENBRACKET:eg,OPENPAREN:ek,PERCENT:eW,PIPE:eF,PLUS:eK,POUND:eG,QUERY:ez,QUOTE:eq,RIGHTCORNERBRACKET:e$,RIGHTWHITECORNERBRACKET:eI,SCHEME:ep,SEMI:eQ,SLASH:eJ,SLASH_SCHEME:ed,SYM:"SYM",TILDE:eY,TLD:"TLD",UNDERSCORE:eX,UTLD:eh,UWORD:eo,WORD:ei,WS:"WS"});let e1=/[a-z]/,e2=/\p{L}/u,e3=/\p{Emoji}/u,e4=/\d/,e5=/\s/;var e6=Object.freeze({__proto__:null,ASCII_LETTER:e1,DIGIT:e4,EMOJI:e3,EMOJI_VARIATION:/\ufe0f/,LETTER:e2,SPACE:e5});let e7=null,e9=null;function e8(e,t){let s=te(t.replace(/[A-Z]/g,e=>e.toLowerCase())),n=s.length,r=[],a=0,i=0;for(;i<n;){let o=e,l=null,u=0,c=null,h=-1,p=-1;for(;i<n&&(l=o.go(s[i]));)(o=l).accepts()?(h=0,p=0,c=o):h>=0&&(h+=s[i].length,p++),u+=s[i].length,a+=s[i].length,i++;a-=h,i-=p,u-=h,r.push({t:c.t,v:t.slice(a-u,a),s:a-u,e:a})}return r}function te(e){let t=[],s=e.length,n=0;for(;n<s;){let r,a=e.charCodeAt(n),i=a<55296||a>56319||n+1===s||(r=e.charCodeAt(n+1))<56320||r>57343?e[n]:e.slice(n,n+2);t.push(i),n+=i.length}return t}function tt(e,t,s,n,r){let a,i=t.length;for(let s=0;s<i-1;s++){let i=t[s];e.j[i]?a=e.j[i]:((a=new et(n)).jr=r.slice(),e.j[i]=a),e=a}return(a=new et(s)).jr=r.slice(),e.j[t[i-1]]=a,a}function ts(e){let t=[],s=[],n=0;for(;n<e.length;){let r=0;for(;"0123456789".indexOf(e[n+r])>=0;)r++;if(r>0){t.push(s.join(""));for(let t=parseInt(e.substring(n,n+r),10);t>0;t--)s.pop();n+=r}else s.push(e[n]),n++}return t}let tn={defaultProtocol:"http",events:null,format:ta,formatHref:ta,nl2br:!1,tagName:"a",target:null,rel:null,validate:!0,truncate:1/0,className:null,attributes:null,ignoreTags:[],render:null};function tr(e,t=null){let s=Object.assign({},tn);e&&(s=Object.assign(s,e instanceof tr?e.o:e));let n=s.ignoreTags,r=[];for(let e=0;e<n.length;e++)r.push(n[e].toUpperCase());this.o=s,t&&(this.defaultRender=t),this.ignoreTags=r}function ta(e){return e}tr.prototype={o:tn,ignoreTags:[],defaultRender:e=>e,check(e){return this.get("validate",e.toString(),e)},get(e,t,s){let n=null!=t,r=this.o[e];return r&&("object"==typeof r?"function"==typeof(r=s.t in r?r[s.t]:tn[e])&&n&&(r=r(t,s)):"function"==typeof r&&n&&(r=r(t,s.t,s))),r},getObj(e,t,s){let n=this.o[e];return"function"==typeof n&&null!=t&&(n=n(t,s.t,s)),n},render(e){let t=e.render(this);return(this.get("render",null,e)||this.defaultRender)(t,e.t,e)}};var ti=Object.freeze({__proto__:null,Options:tr,defaults:tn});function to(e,t){this.t="token",this.v=e,this.tk=t}function tl(e,t){class s extends to{constructor(t,s){super(t,s),this.t=e}}for(let e in t)s.prototype[e]=t[e];return s.t=e,s}to.prototype={isLink:!1,toString(){return this.v},toHref(e){return this.toString()},toFormattedString(e){let t=this.toString(),s=e.get("truncate",t,this),n=e.get("format",t,this);return s&&n.length>s?n.substring(0,s)+"…":n},toFormattedHref(e){return e.get("formatHref",this.toHref(e.get("defaultProtocol")),this)},startIndex(){return this.tk[0].s},endIndex(){return this.tk[this.tk.length-1].e},toObject(e=tn.defaultProtocol){return{type:this.t,value:this.toString(),isLink:this.isLink,href:this.toHref(e),start:this.startIndex(),end:this.endIndex()}},toFormattedObject(e){return{type:this.t,value:this.toFormattedString(e),isLink:this.isLink,href:this.toFormattedHref(e),start:this.startIndex(),end:this.endIndex()}},validate(e){return e.get("validate",this.toString(),this)},render(e){let t=this.toHref(e.get("defaultProtocol")),s=e.get("formatHref",t,this),n=e.get("tagName",t,this),r=this.toFormattedString(e),a={},i=e.get("className",t,this),o=e.get("target",t,this),l=e.get("rel",t,this),u=e.getObj("attributes",t,this),c=e.getObj("events",t,this);return a.href=s,i&&(a.class=i),o&&(a.target=o),l&&(a.rel=l),u&&Object.assign(a,u),{tagName:n,attributes:a,content:r,eventListeners:c}}};let tu=tl("email",{isLink:!0,toHref(){return"mailto:"+this.toString()}}),tc=tl("text"),th=tl("nl"),tp=tl("url",{isLink:!0,toHref(e=tn.defaultProtocol){return this.hasProtocol()?this.v:`${e}://${this.v}`},hasProtocol(){let e=this.tk;return e.length>=2&&e[0].t!==ec&&e[1].t===eM}});var td=Object.freeze({__proto__:null,Base:to,Email:tu,MultiToken:to,Nl:th,Text:tc,Url:tp,createTokenClass:tl});let tm=e=>new et(e);function tf(e,t,s){let n=s[0].s,r=s[s.length-1].e;return new e(t.slice(n,r),s)}let tg="u">typeof console&&console&&console.warn||(()=>{}),tb="until manual call of linkify.init(). Register all schemes and plugins before invoking linkify the first time.",tk={scanner:null,parser:null,tokenQueue:[],pluginQueue:[],customSchemes:[],initialized:!1};function tv(){return et.groups={},tk.scanner=null,tk.parser=null,tk.tokenQueue=[],tk.pluginQueue=[],tk.customSchemes=[],tk.initialized=!1,tk}function tw(e,t){if("function"!=typeof t)throw Error(`linkifyjs: Invalid token plugin ${t} (expects function)`);for(let s=0;s<tk.tokenQueue.length;s++)if(e===tk.tokenQueue[s][0]){tg(`linkifyjs: token plugin "${e}" already registered - will be overwritten`),tk.tokenQueue[s]=[e,t];return}tk.tokenQueue.push([e,t]),tk.initialized&&tg(`linkifyjs: already initialized - will not register token plugin "${e}" ${tb}`)}function ty(e,t){if("function"!=typeof t)throw Error(`linkifyjs: Invalid plugin ${t} (expects function)`);for(let s=0;s<tk.pluginQueue.length;s++)if(e===tk.pluginQueue[s][0]){tg(`linkifyjs: plugin "${e}" already registered - will be overwritten`),tk.pluginQueue[s]=[e,t];return}tk.pluginQueue.push([e,t]),tk.initialized&&tg(`linkifyjs: already initialized - will not register plugin "${e}" ${tb}`)}function tC(e,t=!1){if(tk.initialized&&tg(`linkifyjs: already initialized - will not register custom scheme "${e}" ${tb}`),!/^[0-9a-z]+(-[0-9a-z]+)*$/.test(e))throw Error(`linkifyjs: incorrect scheme format.
1. Must only contain digits, lowercase ASCII letters or "-"
2. Cannot start or end with "-"
3. "-" cannot repeat`);tk.customSchemes.push([e,t])}function tS(){tk.scanner=function(e=[]){let t={};et.groups=t;let s=new et;null==e7&&(e7=ts("aaa1rp3bb0ott3vie4c1le2ogado5udhabi7c0ademy5centure6ountant0s9o1tor4d0s1ult4e0g1ro2tna4f0l1rica5g0akhan5ency5i0g1rbus3force5tel5kdn3l0ibaba4pay4lfinanz6state5y2sace3tom5m0azon4ericanexpress7family11x2fam3ica3sterdam8nalytics7droid5quan4z2o0l2partments8p0le4q0uarelle8r0ab1mco4chi3my2pa2t0e3s0da2ia2sociates9t0hleta5torney7u0ction5di0ble3o3spost5thor3o0s4w0s2x0a2z0ure5ba0by2idu3namex4d1k2r0celona5laycard4s5efoot5gains6seball5ketball8uhaus5yern5b0c1t1va3cg1n2d1e0ats2uty4er2rlin4st0buy5t2f1g1h0arti5i0ble3d1ke2ng0o3o1z2j1lack0friday9ockbuster8g1omberg7ue3m0s1w2n0pparibas9o0ats3ehringer8fa2m1nd2o0k0ing5sch2tik2on4t1utique6x2r0adesco6idgestone9oadway5ker3ther5ussels7s1t1uild0ers6siness6y1zz3v1w1y1z0h3ca0b1fe2l0l1vinklein9m0era3p2non3petown5ital0one8r0avan4ds2e0er0s4s2sa1e1h1ino4t0ering5holic7ba1n1re3c1d1enter4o1rn3f0a1d2g1h0anel2nel4rity4se2t2eap3intai5ristmas6ome4urch5i0priani6rcle4sco3tadel4i0c2y3k1l0aims4eaning6ick2nic1que6othing5ud3ub0med6m1n1o0ach3des3ffee4llege4ogne5m0mbank4unity6pany2re3uter5sec4ndos3struction8ulting7tact3ractors9oking4l1p2rsica5untry4pon0s4rses6pa2r0edit0card4union9icket5own3s1uise0s6u0isinella9v1w1x1y0mru3ou3z2dad1nce3ta1e1ing3sun4y2clk3ds2e0al0er2s3gree4livery5l1oitte5ta3mocrat6ntal2ist5si0gn4v2hl2iamonds6et2gital5rect0ory7scount3ver5h2y2j1k1m1np2o0cs1tor4g1mains5t1wnload7rive4tv2ubai3pont4rban5vag2r2z2earth3t2c0o2deka3u0cation8e1g1mail3erck5nergy4gineer0ing9terprises10pson4quipment8r0icsson6ni3s0q1tate5t1u0rovision8s2vents5xchange6pert3osed4ress5traspace10fage2il1rwinds6th3mily4n0s2rm0ers5shion4t3edex3edback6rrari3ero6i0delity5o2lm2nal1nce1ial7re0stone6mdale6sh0ing5t0ness6j1k1lickr3ghts4r2orist4wers5y2m1o0o0d1tball6rd1ex2sale4um3undation8x2r0ee1senius7l1ogans4ntier7tr2ujitsu5n0d2rniture7tbol5yi3ga0l0lery3o1up4me0s3p1rden4y2b0iz3d0n2e0a1nt0ing5orge5f1g0ee3h1i0ft0s3ves2ing5l0ass3e1obal2o4m0ail3bh2o1x2n1odaddy5ld0point6f2odyear5g0le4p1t1v2p1q1r0ainger5phics5tis4een3ipe3ocery4up4s1t1u0cci3ge2ide2tars5ru3w1y2hair2mburg5ngout5us3bo2dfc0bank7ealth0care8lp1sinki6re1mes5iphop4samitsu7tachi5v2k0t2m1n1ockey4ldings5iday5medepot5goods5s0ense7nda3rse3spital5t0ing5t0els3mail5use3w2r1sbc3t1u0ghes5yatt3undai7ibm2cbc2e1u2d1e0ee3fm2kano4l1m0amat4db2mo0bilien9n0c1dustries8finiti5o2g1k1stitute6urance4e4t0ernational10uit4vestments10o1piranga7q1r0ish4s0maili5t0anbul7t0au2v3jaguar4va3cb2e0ep2tzt3welry6io2ll2m0p2nj2o0bs1urg4t1y2p0morgan6rs3uegos4niper7kaufen5ddi3e0rryhotels6properties14fh2g1h1i0a1ds2m1ndle4tchen5wi3m1n1oeln3matsu5sher5p0mg2n2r0d1ed3uokgroup8w1y0oto4z2la0caixa5mborghini8er3nd0rover6xess5salle5t0ino3robe5w0yer5b1c1ds2ease3clerc5frak4gal2o2xus4gbt3i0dl2fe0insurance9style7ghting6ke2lly3mited4o2ncoln4k2ve1ing5k1lc1p2oan0s3cker3us3l1ndon4tte1o3ve3pl0financial11r1s1t0d0a3u0ndbeck6xe1ury5v1y2ma0drid4if1son4keup4n0agement7go3p1rket0ing3s4riott5shalls7ttel5ba2c0kinsey7d1e0d0ia3et2lbourne7me1orial6n0u2rck0msd7g1h1iami3crosoft7l1ni1t2t0subishi9k1l0b1s2m0a2n1o0bi0le4da2e1i1m1nash3ey2ster5rmon3tgage6scow4to0rcycles9v0ie4p1q1r1s0d2t0n1r2u0seum3ic4v1w1x1y1z2na0b1goya4me2vy3ba2c1e0c1t0bank4flix4work5ustar5w0s2xt0direct7us4f0l2g0o2hk2i0co2ke1on3nja3ssan1y5l1o0kia3rton4w0ruz3tv4p1r0a1w2tt2u1yc2z2obi1server7ffice5kinawa6layan0group9lo3m0ega4ne1g1l0ine5oo2pen3racle3nge4g0anic5igins6saka4tsuka4t2vh3pa0ge2nasonic7ris2s1tners4s1y3y2ccw3e0t2f0izer5g1h0armacy6d1ilips5one2to0graphy6s4ysio5ics1tet2ures6d1n0g1k2oneer5zza4k1l0ace2y0station9umbing5s3m1n0c2ohl2ker3litie5rn2st3r0axi3ess3ime3o0d0uctions8f1gressive8mo2perties3y5tection8u0dential9s1t1ub2w0c2y2qa1pon3uebec3st5racing4dio4e0ad1lestate6tor2y4cipes5d0umbrella9hab3ise0n3t2liance6n0t0als5pair3ort3ublican8st0aurant8view0s5xroth6ich0ardli6oh3l1o1p2o0cks3deo3gers4om3s0vp3u0gby3hr2n2w0e2yukyu6sa0arland6fe0ty4kura4le1on3msclub4ung5ndvik0coromant12ofi4p1rl2s1ve2xo3b0i1s2c0b1haeffler7midt4olarships8ol3ule3warz5ience5ot3d1e0arch3t2cure1ity6ek2lect4ner3rvices6ven3w1x0y3fr2g1h0angrila6rp3ell3ia1ksha5oes2p0ping5uji3w3i0lk2na1gles5te3j1k0i0n2y0pe4l0ing4m0art3ile4n0cf3o0ccer3ial4ftbank4ware6hu2lar2utions7ng1y2y2pa0ce3ort2t3r0l2s1t0ada2ples4r1tebank4farm7c0group6ockholm6rage3e3ream4udio2y3yle4u0cks3pplies3y2ort5rf1gery5zuki5v1watch4iss4x1y0dney4stems6z2tab1ipei4lk2obao4rget4tamotors6r2too4x0i3c0i2d0k2eam2ch0nology8l1masek5nnis4va3f1g1h0d1eater2re6iaa2ckets5enda4ps2res2ol4j0maxx4x2k0maxx5l1m0all4n1o0day3kyo3ols3p1ray3shiba5tal3urs3wn2yota3s3r0ade1ing4ining5vel0ers0insurance16ust3v2t1ube2i1nes3shu4v0s2w1z2ua1bank3s2g1k1nicom3versity8o2ol2ps2s1y1z2va0cations7na1guard7c1e0gas3ntures6risign5mögensberater2ung14sicherung10t2g1i0ajes4deo3g1king4llas4n1p1rgin4sa1ion4va1o3laanderen9n1odka3lvo3te1ing3o2yage5u2wales2mart4ter4ng0gou5tch0es6eather0channel12bcam3er2site5d0ding5ibo2r3f1hoswho6ien2ki2lliamhill9n0dows4e1ners6me2oodside6rk0s2ld3w2s1tc1f3xbox3erox4ihuan4n2xx2yz3yachts4hoo3maxun5ndex5e1odobashi7ga2kohama6u0tube6t1un3za0ppos4ra3ero3ip2m1one3uerich6w2")),null==e9&&(e9=ts("ελ1υ2бг1ел3дети4ею2католик6ом3мкд2он1сква6онлайн5рг3рус2ф2сайт3рб3укр3қаз3հայ3ישראל5קום3ابوظبي5رامكو5لاردن4بحرين5جزائر5سعودية6عليان5مغرب5مارات5یران5بارت2زار4يتك3ھارت5تونس4سودان3رية5شبكة4عراق2ب2مان4فلسطين6قطر3كاثوليك6وم3مصر2ليسيا5وريتانيا7قع4همراه5پاکستان7ڀارت4कॉम3नेट3भारत0म्3ोत5संगठन5বাংলা5ভারত2ৰত4ਭਾਰਤ4ભારત4ଭାରତ4இந்தியா6லங்கை6சிங்கப்பூர்11భారత్5ಭಾರತ4ഭാരതം5ලංකා4คอม3ไทย3ລາວ3გე2みんな3アマゾン4クラウド4グーグル4コム2ストア3セール3ファッション6ポイント4世界2中信1国1國1文网3亚马逊3企业2佛山2信息2健康2八卦2公司1益2台湾1灣2商城1店1标2嘉里0大酒店5在线2大拿2天主教3娱乐2家電2广东2微博2慈善2我爱你3手机2招聘2政务1府2新加坡2闻2时尚2書籍2机构2淡马锡3游戏2澳門2点看2移动2组织机构4网址1店1站1络2联通2谷歌2购物2通販2集团2電訊盈科4飞利浦3食品2餐厅2香格里拉3港2닷넷1컴2삼성2한국2")),ea(s,"'",eR),ea(s,"{",em),ea(s,"}",ef),ea(s,"[",eg),ea(s,"]",eb),ea(s,"(",ek),ea(s,")",ev),ea(s,"<",ew),ea(s,">",ey),ea(s,"（",eC),ea(s,"）",eS),ea(s,"「",eE),ea(s,"」",e$),ea(s,"『",e_),ea(s,"』",eI),ea(s,"＜",eN),ea(s,"＞",ex),ea(s,"&",eT),ea(s,"*",eD),ea(s,"@","AT"),ea(s,"`",eA),ea(s,"^",eL),ea(s,":",eM),ea(s,",",eP),ea(s,"$",eB),ea(s,".","DOT"),ea(s,"=",eH),ea(s,"!",ej),ea(s,"-",eU),ea(s,"%",eW),ea(s,"|",eF),ea(s,"+",eK),ea(s,"#",eG),ea(s,"?",ez),ea(s,'"',eq),ea(s,"/",eJ),ea(s,";",eQ),ea(s,"~",eY),ea(s,"_",eX),ea(s,"\\",eO),ea(s,"・",eV);let n=en(s,e4,"NUM",{[z]:!0});en(n,e4,n);let r=en(n,e1,el,{[Q]:!0}),a=en(n,e2,eu,{[J]:!0}),i=en(s,e1,ei,{[q]:!0});en(i,e4,r),en(i,e1,i),en(r,e4,r),en(r,e1,r);let o=en(s,e2,eo,{[V]:!0});en(o,e1),en(o,e4,a),en(o,e2,o),en(a,e4,a),en(a,e1),en(a,e2,a);let l=ea(s,"\n","NL",{[Z]:!0}),u=ea(s,"\r","WS",{[Z]:!0}),c=en(s,e5,"WS",{[Z]:!0});ea(s,"￼",c),ea(u,"\n",l),ea(u,"￼",c),en(u,e5,c),ea(c,"\r"),ea(c,"\n"),en(c,e5,c),ea(c,"￼",c);let h=en(s,e3,eZ,{[X]:!0});ea(h,"#"),en(h,e3,h),ea(h,"️",h);let p=ea(h,"‍");ea(p,"#"),en(p,e3,h);let d=[[e1,i],[e4,r]],m=[[e1,null],[e2,o],[e4,a]];for(let e=0;e<e7.length;e++)tt(s,e7[e],"TLD",ei,d);for(let e=0;e<e9.length;e++)tt(s,e9[e],eh,eo,m);ee("TLD",{tld:!0,ascii:!0},t),ee(eh,{utld:!0,alpha:!0},t),tt(s,"file",ep,ei,d),tt(s,"mailto",ep,ei,d),tt(s,"http",ed,ei,d),tt(s,"https",ed,ei,d),tt(s,"ftp",ed,ei,d),tt(s,"ftps",ed,ei,d),ee(ep,{scheme:!0,ascii:!0},t),ee(ed,{slashscheme:!0,ascii:!0},t),e=e.sort((e,t)=>e[0]>t[0]?1:-1);for(let t=0;t<e.length;t++){let n=e[t][0],r=e[t][1]?{scheme:!0}:{slashscheme:!0};n.indexOf("-")>=0?r[Y]=!0:e1.test(n)?e4.test(n)?r[Q]=!0:r[q]=!0:r[z]=!0,er(s,n,n,r)}return er(s,"localhost",ec,{ascii:!0}),s.jd=new et("SYM"),{start:s,tokens:Object.assign({groups:t},e0)}}(tk.customSchemes);for(let e=0;e<tk.tokenQueue.length;e++)tk.tokenQueue[e][1]({scanner:tk.scanner});tk.parser=function({groups:e}){let t=e.domain.concat([eT,eD,"AT",eO,eA,eL,eB,eH,eU,"NUM",eW,eF,eK,eG,eJ,"SYM",eY,eX]),s=[eR,eM,eP,"DOT",ej,eW,ez,eq,eQ,ew,ey,em,ef,eb,eg,ek,ev,eC,eS,eE,e$,e_,eI,eN,ex],n=[eT,eR,eD,eO,eA,eL,eB,eH,eU,em,ef,eW,eF,eK,eG,ez,eJ,"SYM",eY,eX],r=tm(),a=ea(r,eY);es(a,n,a),es(a,e.domain,a);let i=tm(),o=tm(),l=tm();es(r,e.domain,i),es(r,e.scheme,o),es(r,e.slashscheme,l),es(i,n,a),es(i,e.domain,i);let u=ea(i,"AT");ea(a,"AT",u),ea(o,"AT",u),ea(l,"AT",u);let c=ea(a,"DOT");es(c,n,a),es(c,e.domain,a);let h=tm();es(u,e.domain,h),es(h,e.domain,h);let p=ea(h,"DOT");es(p,e.domain,h);let d=tm(tu);es(p,e.tld,d),es(p,e.utld,d),ea(u,ec,d);let m=ea(h,eU);ea(m,eU,m),es(m,e.domain,h),es(d,e.domain,h),ea(d,"DOT",p),ea(d,eU,m);let f=ea(i,eU),g=ea(i,"DOT");ea(f,eU,f),es(f,e.domain,i),es(g,n,a),es(g,e.domain,i);let b=tm(tp);es(g,e.tld,b),es(g,e.utld,b),es(b,e.domain,i),es(b,n,a),ea(b,"DOT",g),ea(b,eU,f),ea(b,"AT",u);let k=ea(b,eM),v=tm(tp);es(k,e.numeric,v);let w=tm(tp),y=tm();es(w,t,w),es(w,s,y),es(y,t,w),es(y,s,y),ea(b,eJ,w),ea(v,eJ,w);let C=ea(o,eM),S=ea(l,eM),E=ea(S,eJ),$=ea(E,eJ);es(o,e.domain,i),ea(o,"DOT",g),ea(o,eU,f),es(l,e.domain,i),ea(l,"DOT",g),ea(l,eU,f),es(C,e.domain,w),ea(C,eJ,w),ea(C,ez,w),es($,e.domain,w),es($,t,w),ea($,eJ,w);let _=[[em,ef],[eg,eb],[ek,ev],[ew,ey],[eC,eS],[eE,e$],[e_,eI],[eN,ex]];for(let e=0;e<_.length;e++){let[n,r]=_[e],a=ea(w,n);ea(y,n,a);let i=tm(tp);es(a,t,i);let o=tm();es(a,s,o),ea(a,r,w),es(i,t,i),es(i,s,o),es(o,t,i),es(o,s,o),ea(i,r,w),ea(o,r,w)}return ea(r,ec,b),ea(r,"NL",th),{start:r,tokens:e0}}(tk.scanner.tokens);for(let e=0;e<tk.pluginQueue.length;e++)tk.pluginQueue[e][1]({scanner:tk.scanner,parser:tk.parser});return tk.initialized=!0,tk}function tE(e){return tk.initialized||tS(),function(e,t,s){let n=s.length,r=0,a=[],i=[];for(;r<n;){let o=e,l=null,u=null,c=0,h=null,p=-1;for(;r<n&&!(l=o.go(s[r].t));)i.push(s[r++]);for(;r<n&&(u=l||o.go(s[r].t));)l=null,(o=u).accepts()?(p=0,h=o):p>=0&&p++,r++,c++;if(p<0)(r-=c)<n&&(i.push(s[r]),r++);else{i.length>0&&(a.push(tf(tc,t,i)),i=[]),r-=p,c-=p;let e=h.t,n=s.slice(r-c,r);a.push(tf(e,t,n))}}return i.length>0&&a.push(tf(tc,t,i)),a}(tk.parser.start,e,e8(tk.scanner.start,e))}function t$(e,t=null,s=null){if(t&&"object"==typeof t){if(s)throw Error(`linkifyjs: Invalid link type ${t}; must be a string`);s=t,t=null}let n=new tr(s),r=tE(e),a=[];for(let e=0;e<r.length;e++){let s=r[e];s.isLink&&(!t||s.t===t)&&n.check(s)&&a.push(s.toFormattedObject(n))}return a}function t_(e,t=null){let s=tE(e);return 1===s.length&&s[0].isLink&&(!t||s[0].t===t)}tE.scan=e8;let tI=(function(e){var t=K.get(this);return t||(t=new Map,K.set(this,t)),(t=F(this,t.get(e)||(t.set(e,t=function(e){for(var t,s,n=1,r="",a="",i=[0],o=function(e){1===n&&(e||(r=r.replace(/^\s*\n\s*|\s*\n\s*$/g,"")))?i.push(0,e,r):3===n&&(e||r)?(i.push(3,e,r),n=2):2===n&&"..."===r&&e?i.push(4,e,0):2===n&&r&&!e?i.push(5,0,!0,r):n>=5&&((r||!e&&5===n)&&(i.push(n,0,r,s),n=6),e&&(i.push(n,e,0,s),n=6)),r=""},l=0;l<e.length;l++){l&&(1===n&&o(),o(l));for(var u=0;u<e[l].length;u++)t=e[l][u],1===n?"<"===t?(o(),i=[i],n=3):r+=t:4===n?"--"===r&&">"===t?(n=1,r=""):r=t+r[0]:a?t===a?a="":r+=t:'"'===t||"'"===t?a=t:">"===t?(o(),n=1):n&&("="===t?(n=5,s=r,r=""):"/"===t&&(n<5||">"===e[l][u+1])?(o(),3===n&&(i=i[0]),n=i,(i=i[0]).push(2,0,n),n=0):" "===t||"	"===t||"\n"===t||"\r"===t?(o(),n=2):r+=t),3===n&&"!--"===r&&(n=4,i=i[0])}return o(),i}(e)),t),arguments,[])).length>1?t:t[0]}).bind(I),tN="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",tx={"~":"owner","&":"admin","@":"operator","%":"halfop","+":"voice"},tT={";":"\\:"," ":"\\s","\\":"\\\\","\r":"\\r","\n":"\\n"},tR=Object.fromEntries(Object.entries(tT).map(([e,t])=>[t,e]));function tD(e){let t={};return e.split(";").forEach(e=>{if(!e)return;let s=e.split("=",2),n=s[0],r=null;2===s.length&&(r=s[1].replace(/\\[:s\\rn]/g,e=>tR[e])).endsWith("\\")&&(r=r.slice(0,r.length-1)),t[n]=r}),t}function tO(e){let t=[];for(let s in e){if(void 0===e[s]||null===e[s]){t.push(s);continue}let n=String(e[s]).replace(/[; \\\r\n]/g,e=>tT[e]);t.push(s+"="+n)}return t.join(";")}function tA(e){let t=null,s=e.indexOf("@");s>0&&(t=e.slice(s+1),e=e.slice(0,s));let n=null;return(s=e.indexOf("!"))>0&&(n=e.slice(s+1),e=e.slice(0,s)),{name:e,user:n,host:t}}function tL(e){e.endsWith("\r\n")&&(e=e.slice(0,e.length-2));let t={tags:{},prefix:null,command:null,params:[]};if(e.startsWith("@")){let s=e.indexOf(" ");if(s<0)throw Error("expected a space after tags");t.tags=tD(e.slice(1,s)),e=e.slice(s+1)}if(e.startsWith(":")){let s=e.indexOf(" ");if(s<0)throw Error("expected a space after prefix");t.prefix=tA(e.slice(1,s)),e=e.slice(s+1)}let s=e.indexOf(" ");if(s<0)return t.command=e,t;for(t.command=e.slice(0,s),e=e.slice(s+1);;){if(e.startsWith(":")){t.params.push(e.slice(1));break}if((s=e.indexOf(" "))<0){t.params.push(e);break}t.params.push(e.slice(0,s)),e=e.slice(s+1)}return t}function tM(e){var t;let s,n="";if(e.tags&&Object.keys(e.tags).length>0&&(n+="@"+tO(e.tags)+" "),e.prefix&&(n+=":"+(s=(t=e.prefix).name,t.user&&(s+="!"+t.user),t.host&&(s+="@"+t.host),s)+" "),n+=e.command,e.params&&e.params.length>0){for(let t=0;t<e.params.length-1;t++)n+=" "+e.params[t];let t=String(e.params[e.params.length-1]);0===t.length||t.startsWith(":")||t.indexOf(" ")>=0?n+=" :"+t:n+=" "+t}return n}function tP(e,t){let s;for(s=0;s<e.length&&!(0>t.indexOf(e[s]));s++);return{prefix:e.slice(0,s),name:e.slice(s)}}let tB=(()=>{try{return RegExp("^[\\p{L}0-9]$","u")}catch(e){return RegExp("^[a-zA-Z0-9]$","u")}})(),tH=RegExp("^\\s$");function tj(e){switch(e){case"-":case"_":case"|":return!1;default:return!tB.test(e)}}function tU(e){if(e>="400"&&e<="568")return!0;switch(e){case"902":case"904":case"905":case"906":case"907":case"734":case"FAIL":return!0;default:return!1}}function tW(e){let t=e.getUTCFullYear().toString().padStart(4,"0"),s=(e.getUTCMonth()+1).toString().padStart(2,"0"),n=e.getUTCDate().toString().padStart(2,"0"),r=e.getUTCHours().toString().padStart(2,"0"),a=e.getUTCMinutes().toString().padStart(2,"0"),i=e.getUTCSeconds().toString().padStart(2,"0"),o=e.getUTCMilliseconds().toString().padStart(3,"0");return`${t}-${s}-${n}T${r}:${a}:${i}.${o}Z`}function tF(e){let t;if("PRIVMSG"!==e.command&&"NOTICE"!==e.command)return null;let s=e.params[1];if(!s.startsWith("\x01"))return null;(s=s.slice(1)).endsWith("\x01")&&(s=s.slice(0,-1));let n=s.indexOf(" ");return(t=n>=0?{command:s.slice(0,n),param:s.slice(n+1)}:{command:s,param:""}).command=t.command.toUpperCase(),t}class tK{raw=new Map;parse(e){e.forEach(e=>{if(e.startsWith("-")){let t=e.slice(1);this.raw.delete(t.toUpperCase());return}let t=e.indexOf("="),s=e,n="";t>=0&&(s=e.slice(0,t),n=e.slice(t+1).replace(/\\x[0-9A-Z]{2}/gi,e=>String.fromCharCode(parseInt(e.slice(2),16)))),s=s.toUpperCase(),this.raw.set(s,n)})}caseMapping(){let e=this.raw.get("CASEMAPPING");if(!e)return tG.RFC1459;let t=tG.byName(e);return t||(console.error("Unsupported case-mapping '"+e+"', falling back to RFC 1459"),tG.RFC1459)}monitor(){let e=this.raw.get("MONITOR");return void 0===e?0:""===e?1/0:parseInt(e,10)}whox(){return this.raw.has("WHOX")}membershipModes(){let e=this.raw.get("PREFIX");return function(e){if("("!==e[0])throw Error("malformed ISUPPORT PREFIX value: expected opening parenthesis");let t=e.indexOf(")");if(t<0)throw Error("malformed ISUPPORT PREFIX value: expected closing parenthesis");let s=e.length-t-1,n=[];for(let r=0;r<s;r++){let s=e[r+1],a=e[t+r+1];n.push({mode:s,prefix:a})}return n}(void 0!==e?e:"(ov)@+")}chanTypes(){let e=this.raw.get("CHANTYPES");return void 0!==e?e:"#&+!"}statusMsg(){return this.raw.get("STATUSMSG")||""}network(){return this.raw.get("NETWORK")}chatHistory(){let e=this.raw.get("CHATHISTORY");if(void 0===e)return 0;let t=parseInt(e,10);return t<=0?1/0:t}bouncerNetID(){return this.raw.get("BOUNCER_NETID")}chanModes(){let e=["beI","k","l","imnst"],t=this.raw.get("CHANMODES");if(void 0===t)return e;let s=t.split(",");return 4!==s.length?(console.error("Invalid CHANMODES: ",this.raw.get("CHANMODES")),e):s}bot(){return this.raw.get("BOT")}userLen(){let e=this.raw.get("USERLEN");return void 0===e?20:parseInt(e,10)}hostLen(){let e=this.raw.get("HOSTLEN");return void 0===e?63:parseInt(e,10)}lineLen(){let e=this.raw.get("LINELEN");return void 0===e?512:parseInt(e,10)}filehost(){return this.raw.get("SOJU.IM/FILEHOST")}}let tG={ASCII(e){let t="";for(let s=0;s<e.length;s++){let n=e[s];"A"<=n&&n<="Z"&&(n=n.toLowerCase()),t+=n}return t},RFC1459(e){let t="";for(let s=0;s<e.length;s++){let n=e[s];"A"<=n&&n<="Z"?n=n.toLowerCase():"{"===n?n="[":"}"===n?n="]":"\\"===n?n="|":"~"===n&&(n="^"),t+=n}return t},RFC1459Strict(e){let t="";for(let s=0;s<e.length;s++){let n=e[s];"A"<=n&&n<="Z"?n=n.toLowerCase():"{"===n?n="[":"}"===n?n="]":"\\"===n&&(n="|"),t+=n}return t},byName(e){switch(e){case"ascii":return tG.ASCII;case"rfc1459":return tG.RFC1459;case"rfc1459-strict":return tG.RFC1459Strict}return null}};function tz(e,t){return{next:()=>{let{value:s,done:n}=e.next();return n?{done:!0}:{value:t(s),done:!1}},[Symbol.iterator](){return this}}}class tq{caseMap;map;constructor(e,t){if(e instanceof tq&&(e.caseMap===t||!t))this.caseMap=e.caseMap,this.map=new Map(e.map);else{if(!t)throw Error("Missing case-mapping when creating CaseMapMap");if(this.caseMap=t,this.map=new Map,e)for(let[t,s]of e)this.set(t,s)}}get size(){return this.map.size}has(e){return this.map.has(this.caseMap(e))}get(e){let t=this.map.get(this.caseMap(e));if(t)return t.value}set(e,t){this.map.set(this.caseMap(e),{key:e,value:t})}delete(e){this.map.delete(this.caseMap(e))}entries(){return tz(this.map.values(),e=>[e.key,e.value])}keys(){return tz(this.map.values(),e=>e.key)}values(){return tz(this.map.values(),e=>e.value)}[Symbol.iterator](){return this.entries()}}function tV(e,t){let s=e.batch;for(;s;){if(s.type===t)return s;s=s.parent}return null}function tQ(e,t){return!!e&&e!==t&&"realname"!==e.toLowerCase()&&"unknown"!==e.toLowerCase()&&"fullname"!==e.toLowerCase()}function tJ(e){let t,s;if(!e.startsWith("irc://")&&!e.startsWith("ircs://"))return null;let n=(e=e.slice(e.indexOf(":")+3)).indexOf("/");n<0?(t=e,e=""):(t=e.slice(0,n),e=e.slice(n+1));let r=t;if((n=t.indexOf("@"))>=0&&(r=t.slice(n+1)),(n=e.indexOf("?"))>=0&&(e=e.slice(0,n)),(n=e.indexOf(","))>=0){let t=e.slice(n+1).split(",");e=e.slice(0,n),t.indexOf("isuser")>=0?s="user":t.indexOf("ischannel")>=0&&(s="channel")}let a=decodeURIComponent(e);return s||(s=a.startsWith("#")?"channel":"user"),{host:r,enttype:s,entity:a}}function tY({host:e,enttype:t,entity:s}={}){let n="irc://"+(e=e||"")+"/"+encodeURIComponent(s=s||"");return t&&(n+=",is"+t),n}class tX{available=new Map;enabled=new Set;addAvailable(e){e.split(" ").forEach(e=>{let t=e.indexOf("="),s=e,n="";t>=0&&(s=e.slice(0,t),n=e.slice(t+1)),this.available.set(s.toLowerCase(),n)})}parse(e){if("CAP"!==e.command)return;let t=e.params[1],s=e.params.slice(2);switch(t){case"LS":this.addAvailable(s[s.length-1]);break;case"NEW":this.addAvailable(s[0]);break;case"DEL":s[0].split(" ").forEach(e=>{e=e.toLowerCase(),this.available.delete(e),this.enabled.delete(e)});break;case"ACK":s[0].split(" ").forEach(e=>{(e=e.toLowerCase()).startsWith("-")?this.enabled.delete(e.slice(1)):this.enabled.add(e)})}}requestAvailable(e){return 0===(e=e.filter(e=>this.available.has(e)&&!this.enabled.has(e))).length?null:{command:"CAP",params:["REQ",e.join(" ")]}}}let tZ=["account-notify","away-notify","batch","chghost","echo-message","extended-join","extended-monitor","invite-notify","labeled-response","message-tags","multi-prefix","sasl","server-time","setname","draft/account-registration","draft/chathistory","draft/extended-monitor","draft/message-redaction","draft/read-marker","soju.im/bouncer-networks"],t0={1001:"going away",1002:"protocol error",1003:"unsupported data",1005:"no status code received",1006:"abnormal closure",1007:"invalid frame payload data",1008:"policy violation",1009:"message too big",1010:"missing mandatory extension",1011:"internal server error",1015:"TLS handshake failed"},t1={channel:"c",username:"u",hostname:"h",server:"s",nick:"n",flags:"f",account:"a",realname:"r"},t2={name:"*"},t3=0,t4=0,t5=0;class t6 extends Error{constructor(e){super(e.params.length>0?e.params[e.params.length-1]:`unknown error (${e.command})`),this.msg=e}}class t7 extends Error{constructor(e){let t="Connection error",s=t0[e];s&&(t+=" ("+s+")"),super(t)}}class t9{n=0;constructor(e,t){this.base=e,this.max=t}reset(){this.n=0}next(){if(0===this.n)return this.n=1,0;let e=this.n*this.base;return e>this.max?e=this.max:this.n*=2,e}}class t8 extends EventTarget{static Status={DISCONNECTED:"disconnected",CONNECTING:"connecting",REGISTERING:"registering",REGISTERED:"registered"};status=t8.Status.DISCONNECTED;serverPrefix=t2;nick=null;supportsCap=!1;caps=new tX;isupport=new tK;ws=null;params={url:null,username:null,realname:null,nick:null,pass:null,saslPlain:null,saslExternal:!1,saslOauthBearer:null,bouncerNetwork:null,ping:0,eventPlayback:!0};debug=!1;batches=new Map;autoReconnect=!0;reconnectTimeoutID=null;reconnectBackoff=new t9(1e4,6e5);lastReconnectDate=new Date(0);pingIntervalID=null;pendingCmds={WHO:Promise.resolve(null),CHATHISTORY:Promise.resolve(null)};cm=tG.RFC1459;monitored=new tq(null,tG.RFC1459);pendingLists=new tq(null,tG.RFC1459);whoxQueries=new Map;constructor(e){super(),this.handleOnline=this.handleOnline.bind(this),this.params={...this.params,...e},this.reconnect()}reconnect(){let e=this.autoReconnect;this.disconnect(),this.autoReconnect=e,console.log("Connecting to "+this.params.url),this.setStatus(t8.Status.CONNECTING),this.lastReconnectDate=new Date;try{this.ws=new WebSocket(this.params.url)}catch(e){console.error("Failed to create connection:",e),setTimeout(()=>{this.dispatchError(Error("Failed to create connection",{cause:e})),this.setStatus(t8.Status.DISCONNECTED)},0);return}this.ws.addEventListener("open",this.handleOpen.bind(this)),this.ws.addEventListener("message",e=>{try{this.handleMessage(e)}catch(e){this.dispatchError(e),this.disconnect()}}),this.ws.addEventListener("close",e=>{if(console.log("Connection closed (code: "+e.code+")"),1e3!==e.code&&1001!==e.code&&this.dispatchError(new t7(e.code)),this.ws=null,this.setStatus(t8.Status.DISCONNECTED),this.nick=null,this.serverPrefix=t2,this.caps=new tX,this.batches=new Map,Object.keys(this.pendingCmds).forEach(e=>{this.pendingCmds[e]=Promise.resolve(null)}),this.isupport=new tK,this.monitored=new tq(null,tG.RFC1459),this.autoReconnect)if(window.addEventListener("online",this.handleOnline),navigator.onLine){let e=this.reconnectBackoff.next();new Date().getTime()-this.lastReconnectDate.getTime()<1e4&&(e=Math.max(e,1e4)),console.info("Reconnecting to server in "+e/1e3+" seconds"),clearTimeout(this.reconnectTimeoutID),this.reconnectTimeoutID=setTimeout(()=>{this.reconnect()},e)}else console.info("Waiting for network to go back online")})}disconnect(){this.autoReconnect=!1,clearTimeout(this.reconnectTimeoutID),this.reconnectTimeoutID=null,window.removeEventListener("online",this.handleOnline),this.setPingInterval(0),this.ws&&this.ws.close(1e3)}setStatus(e){this.status!==e&&(this.status=e,this.dispatchEvent(new CustomEvent("status")))}dispatchError(e){this.dispatchEvent(new CustomEvent("error",{detail:e}))}handleOnline(){window.removeEventListener("online",this.handleOnline),this.autoReconnect&&this.status===t8.Status.DISCONNECTED&&this.reconnect()}handleOpen(){console.log("Connection opened"),this.setStatus(t8.Status.REGISTERING),this.reconnectBackoff.reset(),this.setPingInterval(this.params.ping),this.nick=this.params.nick,this.send({command:"CAP",params:["LS","302"]}),this.params.pass&&this.send({command:"PASS",params:[this.params.pass]}),this.send({command:"NICK",params:[this.nick]}),this.send({command:"USER",params:[this.params.username,"0","*",this.params.realname]})}pushPendingList(e,t){let s=this.pendingLists.get(e);s||(s=[],this.pendingLists.set(e,s)),s.push(t)}endPendingList(e,t){t.list=this.pendingLists.get(e)||[],this.pendingLists.delete(e)}handleMessage(e){if("string"!=typeof e.data){console.error("Received unsupported data type:",e.data),this.ws.close(1003);return}let t=e.data;this.debug&&console.debug("Received:",t);let s=tL(t);s.prefix||(s.prefix=this.serverPrefix),s.tags||(s.tags={});let n=null;s.tags.batch&&(n=this.batches.get(s.tags.batch))&&(s.batch=n);let r=null;switch(s.command){case"001":if(this.params.saslPlain&&!this.supportsCap){this.dispatchError(Error("Server doesn't support SASL PLAIN")),this.disconnect();return}s.prefix&&(this.serverPrefix=s.prefix),this.nick=s.params[0],console.log("Registration complete"),this.setStatus(t8.Status.REGISTERED);break;case"005":let a=this.isupport.monitor(),i=s.params.slice(1,-1);this.isupport.parse(i),this.updateCaseMapping();let o=this.isupport.monitor();if(0===a&&this.monitored.size>0&&o>0){let e=Array.from(this.monitored.keys()).slice(0,o);this.send({command:"MONITOR",params:["+",e.join(",")]})}break;case"376":case"422":this.isupport.raw.has("CASEMAPPING")||this.updateCaseMapping();break;case"CAP":this.handleCap(s);break;case"AUTHENTICATE":let l=s.params[0];"+"!==l&&(this.dispatchError(Error("Expected an empty challenge, got: "+l)),this.send({command:"AUTHENTICATE",params:["*"]}));break;case"900":console.log("Logged in");break;case"901":console.log("Logged out");break;case"353":this.pushPendingList("NAMES "+s.params[2],s);break;case"366":this.endPendingList("NAMES "+s.params[1],s);break;case"311":case"312":case"313":case"317":case"319":this.pushPendingList("WHOIS "+s.params[1],s);break;case"318":this.endPendingList("WHOIS "+s.params[1],s);break;case"352":case"354":this.pushPendingList("WHO",s);break;case"315":this.endPendingList("WHO",s);break;case"PING":this.send({command:"PONG",params:[s.params[0]]});break;case"NICK":let u=s.params[0];this.isMyNick(s.prefix.name)&&(this.nick=u);break;case"BATCH":let c=s.params[0].startsWith("+"),h=s.params[0].slice(1);if(c){let e={name:h,type:s.params[1],params:s.params.slice(2),tags:s.tags,parent:n};this.batches.set(h,e)}else r=h;break;case"ERROR":this.dispatchError(new t6(s)),this.disconnect();break;case"464":case"432":case"433":case"436":case"437":case"463":case"465":this.dispatchError(new t6(s)),this.status!==t8.Status.REGISTERED&&this.disconnect();break;case"FAIL":if(this.status===t8.Status.REGISTERED)break;"BOUNCER"===s.params[0]&&"BIND"===s.params[2]&&(this.dispatchError(Error("Failed to bind to bouncer network",{cause:new t6(s)})),this.disconnect()),"ACCOUNT_REQUIRED"===s.params[1]&&(this.dispatchError(new t6(s)),this.disconnect())}this.dispatchEvent(new CustomEvent("message",{detail:{message:s,batch:n}})),r&&this.batches.delete(r)}async authenticate(e,t){let s;if(!this.supportsSASL(e))throw Error(`${e} authentication not supported by the server`);switch(console.log(`Starting SASL ${e} authentication`),e){case"PLAIN":s="\0"+t.username+"\0"+t.password;break;case"EXTERNAL":s="";break;case"OAUTHBEARER":s="n,,\x01auth=Bearer "+t.token+"\x01\x01";break;default:throw Error(`Unknown authentication mechanism '${e}'`)}let n=this.roundtrip({command:"AUTHENTICATE",params:[e]},e=>{switch(e.command){case"903":return!0;case"902":case"904":case"905":case"906":case"907":throw new t6(e)}});for(let e of function(e){let t=function(e){if(!window.TextEncoder)return btoa(e);let t=new TextEncoder().encode(e),s=t.length%3,n="";for(let e=0;e<t.length-s;e+=3){let s=(t[e]<<16)+(t[e+1]<<8)+t[e+2];n+=tN[s>>18&63],n+=tN[s>>12&63],n+=tN[s>>6&63],n+=tN[63&s]}if(1===s){let e=t[t.length-1];n+=tN[e>>2],n+=tN[e<<4&63],n+="=="}else if(2===s){let e=(t[t.length-2]<<8)+t[t.length-1];n+=tN[e>>10],n+=tN[e>>4&63],n+=tN[e<<2&63],n+="="}return n}(e),s=[];for(let e=0;e<=t.length;e+=400){let n=t.substring(e,e+400);s.push({command:"AUTHENTICATE",params:[n||"+"]})}return s}(s))this.send(e);await n}who(e,t){let s=[e],n="",r="";t&&this.isupport.whox()&&(n="t",t.fields&&t.fields.forEach(e=>{if(!t1[e])throw Error(`Unknown WHOX field ${e}`);n+=t1[e]}),r=String(t5%1e3),t5++,s.push(`%${n},${r}`),this.whoxQueries.set(r,n));let a={command:"WHO",params:s},i=[],o=this.pendingCmds.WHO.then(()=>this.roundtrip(a,t=>{switch(t.command){case"352":t.internal=!0,i.push(this.parseWhoReply(t));break;case"354":if(t.params.length!==n.length+1||t.params[1]!==r)break;t.internal=!0,i.push(this.parseWhoReply(t));break;case"315":if(t.params[1]===e)return t.internal=!0,i}}).finally(()=>{this.whoxQueries.delete(r)}));return this.pendingCmds.WHO=o.catch(()=>{}),o}parseWhoReply(e){switch(e.command){case"352":let t=e.params[e.params.length-1];return{username:e.params[2],hostname:e.params[3],server:e.params[4],nick:e.params[5],flags:e.params[6],realname:t.slice(t.indexOf(" ")+1)};case"354":let s=e.params[1],n=this.whoxQueries.get(s);if(!n)throw Error("Unknown WHOX token: "+s);let r={},a=0;return Object.keys(t1).forEach(t=>{!(0>n.indexOf(t1[t]))&&(r[t]=e.params[2+a],a++)}),"0"===r.account&&(r.account=null),r;default:throw Error("Not a WHO reply: "+e.command)}}async whois(e){let t=this.cm(e),s=await this.roundtrip({command:"WHOIS",params:[e]},e=>{let s;switch(e.command){case"318":if(s=e.params[1],this.cm(s)===t)return e;break;case"401":if(s=e.params[1],this.cm(s)===t)throw new t6(e)}}),n={};return s.list.forEach(e=>{n[e.command]=e}),n}supportsSASL(e){let t=this.caps.available.get("sasl");return void 0!==t&&t.split(",").includes(e)}checkAccountRegistrationCap(e){let t=this.caps.available.get("draft/account-registration");return void 0!==t&&t.split(",").includes(e)}requestCaps(){let e=[].concat(tZ);this.params.bouncerNetwork||e.push("soju.im/bouncer-networks-notify"),this.params.eventPlayback&&e.push("draft/event-playback");let t=this.caps.requestAvailable(e);t&&this.send(t)}handleCap(e){this.caps.parse(e);let t=e.params[1],s=e.params.slice(2);switch(t){case"LS":if(this.supportsCap=!0,"*"===s[0])break;if(console.log("Available server caps:",this.caps.available),this.requestCaps(),this.status!==t8.Status.REGISTERED){if(this.caps.available.has("sasl")){let e;this.params.saslPlain?e=this.authenticate("PLAIN",this.params.saslPlain):this.params.saslExternal?e=this.authenticate("EXTERNAL"):this.params.saslOauthBearer&&(e=this.authenticate("OAUTHBEARER",this.params.saslOauthBearer)),(e||Promise.resolve()).catch(e=>{this.dispatchError(e),this.disconnect()})}this.caps.available.has("soju.im/bouncer-networks")&&this.params.bouncerNetwork&&this.send({command:"BOUNCER",params:["BIND",this.params.bouncerNetwork]}),this.send({command:"CAP",params:["END"]})}break;case"NEW":console.log("Server added available caps:",s[0]),this.requestCaps();break;case"DEL":console.log("Server removed available caps:",s[0]);break;case"ACK":console.log("Server ack'ed caps:",s[0]);break;case"NAK":console.log("Server nak'ed caps:",s[0]),this.status!==t8.Status.REGISTERED&&this.send({command:"CAP",params:["END"]})}}send(e){if(!this.ws)throw Error("Failed to send IRC message "+e.command+": socket is closed");let t=tM(e);this.ws.send(t),this.debug&&console.debug("Sent:",t)}updateCaseMapping(){this.cm=this.isupport.caseMapping(),this.pendingLists=new tq(this.pendingLists,this.cm),this.monitored=new tq(this.monitored,this.cm)}isServer(e){return"*"===e||this.cm(e)===this.cm(this.serverPrefix.name)}isMyNick(e){return this.cm(e)===this.cm(this.nick)}isChannel(e){return this.isupport.chanTypes().indexOf(e[0])>=0}isNick(e){return!this.isServer(e)&&!this.isChannel(e)&&!e.startsWith("$")}setPingInterval(e){clearInterval(this.pingIntervalID),this.pingIntervalID=null,e<=0||(this.pingIntervalID=setInterval(()=>{this.ws&&this.send({command:"PING",params:["gamja"]})},1e3*e))}roundtrip(e,t){let s,n=e.command;return this.caps.enabled.has("labeled-response")&&(s=String(++t4),e.tags={...e.tags,label:s}),new Promise((r,a)=>{let i,o=e=>{let o,l=e.detail.message,u=function(e){if(e.tags.label)return e.tags.label;let t=e.batch;for(;t;){if(t.tags.label)return t.tags.label;t=t.parent}return null}(l);if(u&&u!==s)return;let c=!1;switch(l.command){case"FAIL":c=l.params[0]===n;break;case"400":case"421":case"461":case"263":c=l.params[1]===n}if(c){i(),a(new t6(l));return}try{o=t(l)}catch(e){i(),a(e)}o&&(i(),r(o))},l=()=>{this.status===t8.Status.DISCONNECTED&&(i(),a(Error("Connection closed")))};i=()=>{this.removeEventListener("message",o),this.removeEventListener("status",l)},this.addEventListener("message",o,{capture:!0}),this.addEventListener("status",l),this.send(e)})}async join(e,t){let s=[e];t&&s.push(t),await this.roundtrip({command:"JOIN",params:s},t=>{switch(t.command){case"403":case"405":case"475":case"474":case"471":case"473":if(this.cm(t.params[1])===this.cm(e))throw new t6(t);break;case"JOIN":if(this.isMyNick(t.prefix.name)&&this.cm(t.params[0])===this.cm(e))return!0}})}fetchBatch(e,t){let s=null,n=[];return this.roundtrip(e,e=>{if(s){let t=e.batch;for(;t;){if(t.name===s){n.push(e);break}t=t.parent}}if("BATCH"!==e.command)return;let r=e.params[0].startsWith("+"),a=e.params[0].slice(1);if(r&&e.params[1]===t){s=a;return}if(!r&&a===s)return{...this.batches.get(a),messages:n}})}roundtripChatHistory(e){let t=this.pendingCmds.CHATHISTORY.then(async()=>(await this.fetchBatch({command:"CHATHISTORY",params:e},"chathistory")).messages);return this.pendingCmds.CHATHISTORY=t.catch(()=>{}),t}async fetchHistoryBefore(e,t,s){let n=Math.min(s,this.isupport.chatHistory()),r=await this.roundtripChatHistory(["BEFORE",e,"timestamp="+t,n]);return{messages:r,more:r.length>=n}}async fetchHistoryBetween(e,t,s,n){let r=Math.min(n,this.isupport.chatHistory()),a=["AFTER",e,"timestamp="+t.time,r],i=await this.roundtripChatHistory(a);if((n-=i.length)<=0)throw Error("Cannot fetch all chat history: too many messages");return i.length>=r?(t={...t,time:i[i.length-1].tags.time},await this.fetchHistoryBetween(e,t,s,n)):{messages:i}}async fetchHistoryTargets(e,t){return(await this.fetchBatch({command:"CHATHISTORY",params:["TARGETS","timestamp="+e,"timestamp="+t,1e3]},"draft/chathistory-targets")).messages.map(e=>(console.assert("CHATHISTORY"===e.command&&"TARGETS"===e.params[0]),{name:e.params[1],latestMessage:e.params[2]}))}async listBouncerNetworks(){let e=await this.fetchBatch({command:"BOUNCER",params:["LISTNETWORKS"]},"soju.im/bouncer-networks"),t=new Map;for(let s of e.messages){console.assert("BOUNCER"===s.command&&"NETWORK"===s.params[0]);let e=s.params[1],n=tD(s.params[2]);t.set(e,n)}return t}monitor(e){this.monitored.has(e)||(this.monitored.set(e,!0),this.monitored.size+1>this.isupport.monitor()||this.send({command:"MONITOR",params:["+",e]}))}unmonitor(e){!this.monitored.has(e)||(this.monitored.delete(e),0>=this.isupport.monitor()||this.send({command:"MONITOR",params:["-",e]}))}createBouncerNetwork(e){let t={command:"BOUNCER",params:["ADDNETWORK",tO(e)]};return this.roundtrip(t,e=>{if("BOUNCER"===e.command&&"ADDNETWORK"===e.params[0])return e.params[1]})}registerAccount(e,t){return this.roundtrip({command:"REGISTER",params:["*",e||"*",t]},e=>{if("REGISTER"===e.command)return{verificationRequired:"VERIFICATION_REQUIRED"===e.params[0],account:e.params[1],message:e.params[2]}})}verifyAccount(e,t){return this.roundtrip({command:"VERIFY",params:[e,t]},e=>{if("VERIFY"===e.command)return{message:e.params[2]}})}supportsReadMarker(){return this.caps.enabled.has("draft/read-marker")}fetchReadMarker(e){this.send({command:"MARKREAD",params:[e]})}setReadMarker(e,t){this.send({command:"MARKREAD",params:[e,"timestamp="+t]})}async ping(){let e="gamja-"+String(++t3);await this.roundtrip({command:"PING",params:[e]},t=>"PONG"===t.command&&t.params[1]===e)}}function se(e){let t=[];for(let s in e)t.push(encodeURIComponent(s)+"="+encodeURIComponent(e[s]));return t.join("&")}async function st(e){let t;try{if(!(t=await fetch(e+"/.well-known/oauth-authorization-server")).ok)throw Error(`HTTP error: ${t.status} ${t.statusText}`)}catch(s){console.warn("OAuth 2.0 server doesn't support Authorization Server Metadata (retrying with OpenID Connect Discovery): ",s),t=await fetch(e+"/.well-known/openid-configuration")}if(!t.ok)throw Error(`HTTP error: ${t.status} ${t.statusText}`);let s=await t.json();if(!s.issuer)throw Error("Missing issuer in response");if(!s.authorization_endpoint||!s.token_endpoint)throw Error("Missing authorization_endpoint in response");if(!s.response_types_supported.includes("code"))throw Error("Server doesn't support authorization code response type");return s}function ss(e,t){let s={"Content-Type":"application/x-www-form-urlencoded",Accept:"application/json"};return t&&(s.Authorization="Basic "+btoa(encodeURIComponent(e)+":"+encodeURIComponent(t))),s}async function sn({serverMetadata:e,redirectUri:t,code:s,clientId:n,clientSecret:r}){let a={grant_type:"authorization_code",code:s,redirect_uri:t};r||(a.client_id=n);let i=await fetch(e.token_endpoint,{method:"POST",headers:ss(n,r),body:se(a)});if(!i.ok)throw Error(`HTTP error: ${i.status} ${i.statusText}`);if((a=await i.json()).error)throw Error("Authentication failed: "+(a.error_description||a.error));return a}async function sr({serverMetadata:e,token:t,clientId:s,clientSecret:n}){let r=await fetch(e.introspection_endpoint,{method:"POST",headers:ss(s,n),body:se({token:t})});if(!r.ok)throw Error(`HTTP error: ${r.status} ${r.statusText}`);let a=await r.json();if(!a.active)throw Error("Expired token");return a}G.options.defaults.defaultProtocol="https",G.registerCustomProtocol("irc"),G.registerCustomProtocol("ircs"),G.registerCustomProtocol("geo",!0);let sa=G.createTokenClass("ircChannel",{isLink:!0,toHref(){return"irc:///"+this.v}});function si(e,t){let s=G.find(e),n=[],r=0;s.forEach(s=>{if(!s.isLink)return;let a=e.substring(r,s.start);n.push(a),n.push(tI`
			<a
				href=${s.href}
				target="_blank"
				rel="noreferrer noopener"
				onClick=${t}
			>${s.value}</a>
		`),r=s.end});let a=e.substring(r);return n.push(a),n}function so(e){return e>="0"&&e<="9"}function sl(e){if(e.length<6)return!1;for(let t=0;t<6;t++){let s=e[t].toUpperCase();if(!(s>="0"&&s<="9"||s>="A"&&s<="F"))return!1}return!0}function su(e){let t="";for(let s=0;s<e.length;s++){let n=e[s];switch(n){case"\x02":case"\x1d":case"\x1f":case"\x1e":case"\x11":case"\x16":case"\x0f":break;case"\x03":if(!so(e[s+1]))break;so(e[++s+1])&&s++,","===e[s+1]&&so(e[s+2])&&so(e[(s+=2)+1])&&s++;break;case"\x04":if(!sl(e.slice(s+1)))break;","===e[(s+=6)+1]&&sl(e.slice(s+2))&&(s+=7);break;default:t+=n}}return t}G.registerPlugin("ircChannel",({scanner:e,parser:t})=>{let{POUND:s,UNDERSCORE:n,DOT:r,HYPHEN:a}=e.tokens,{alphanumeric:i}=e.tokens.groups,o=t.start.tt(s),l=new G.State(sa),u=l.tt(r);o.ta(i,l),o.tt(s,l),o.tt(n,l),o.tt(r,u),o.tt(a,l),l.ta(i,l),l.tt(s,l),l.tt(n,l),l.tt(a,l),u.ta(i,l)});let sc="server",sh="channel",sp="nick",sd=t8.Status,sm={NONE:"",MESSAGE:"message",HIGHLIGHT:"highlight",compare(e,t){let s={[sm.NONE]:0,[sm.MESSAGE]:1,[sm.HIGHLIGHT]:2};return s[e]-s[t]},union:(e,t)=>sm.compare(e,t)>0?e:t},sf="delivered",sg="read",sb="fold",sk="hide",sv=function(e){function t(e){var s,n;return this.getChildContext||(s=new Set,(n={})[t.__c]=this,this.getChildContext=function(){return n},this.componentWillUnmount=function(){s=null},this.shouldComponentUpdate=function(e){this.props.value!=e.value&&s.forEach(function(e){e.__e=!0,O(e)})},this.sub=function(e){s.add(e);var t=e.componentWillUnmount;e.componentWillUnmount=function(){s&&s.delete(e),t&&t.call(e)}}),e.children}return t.__c="__cC"+w++,t.__=e,t.Provider=t.__l=(t.Consumer=function(e,t){return e.children(t)}).contextType=t,t}("settings");function sw(e,t=null){let s=t?.host;switch(e.type){case sc:return tY({host:s});case sh:return tY({host:s,entity:e.name});case sp:return tY({host:s,entity:e.name,enttype:"user"})}throw Error("Unknown buffer type: "+e.type)}function sy(e,t,s=null){let n=sw(e,s);return t.tags.msgid?n+"?msgid="+encodeURIComponent(t.tags.msgid):t.tags.time?n+"?timestamp="+encodeURIComponent(t.tags.time):null}function sC(e,t){let s=e.name;return t&&t.name&&t.name!==t.host?t.name:s||(t?t.name||t.host||"server":e.isBouncer?"bouncer":"server")}function sS(e){if(!e.tags.time)throw Error("Missing time message tag");return{time:e.tags.time}}function sE(e,t){if(!t)return!1;if(!e)return!0;if(!e.time||!t.time)throw Error("Missing receipt time");return e.time<=t.time}function s$(e,t){if(!t)return!1;if(!e.tags.time)throw Error("Missing time message tag");if(!t.time)throw Error("Missing receipt time");return e.tags.time<=t.time}function s_(e,t){let s;if(s="function"==typeof t?t(e,e):t,e!==s&&s)return{...e,...s}}function sI(e){return e.type===sc}function sN(e,t){let s=0;for(;s<e.length&&e[s]===t;++s);return e.substring(s)}function sx(e,t){let s=e.servers.get(t.server),n=e.bouncerNetworks.get(s.bouncerNetID);return n?sC(s,n):null}let sT=0,sR=0,sD=0,sO={create:()=>({servers:new Map,buffers:new Map,activeBuffer:null,bouncerNetworks:new Map,settings:{secondsInTimestamps:!0,bufferEvents:sb}}),updateServer(e,t,s){let n=e.servers.get(t);if(!n)return;let r=s_(n,s);if(!r)return;let a=new Map(e.servers);return a.set(t,r),{servers:a}},updateBuffer(e,t,s){let n=sO.getBuffer(e,t);if(!n)return;let r=s_(n,s);if(!r)return;let a=new Map(e.buffers);return a.set(n.id,r),{buffers:a}},getActiveServerID(e){let t=e.buffers.get(e.activeBuffer);return t?t.server:null},getBuffer(e,t){switch(typeof t){case"number":return e.buffers.get(t);case"object":if(t.id)return e.buffers.get(t.id);let s=t.server,n=t.name;s||(s=sO.getActiveServerID(e)),n||(n="*");let r=tG.RFC1459,a=e.servers.get(s);a&&(r=a.cm);let i=r(n);for(let t of e.buffers.values())if(t.server===s&&r(t.name)===i)return t;return null;default:throw Error("Invalid buffer ID type: "+typeof t)}},createServer(e){let t=++sT,s=new Map(e.servers);return s.set(t,{id:t,name:null,status:sd.DISCONNECTED,cm:tG.RFC1459,users:new tq(null,tG.RFC1459),account:null,supportsSASLPlain:!1,supportsAccountRegistration:!1,reliableUserAccounts:!1,statusMsg:null,membershipModes:null,isBouncer:!1,bouncerNetID:null}),[t,{servers:s}]},createBuffer(e,t,s,n){let r,a=sO.getBuffer(e,{server:s,name:t});if(a)return[a.id,null];let i=++sR;r="*"===t?sc:n.isChannel(t)?sh:sp;let o=Array.from(e.buffers.values());return o.push({id:i,name:t,type:r,server:s,serverInfo:null,joined:!1,topic:null,hasInitialWho:!1,members:new tq(null,n.cm),messages:[],redacted:new Set,unread:sm.NONE,prevReadReceipt:null}),[i,{buffers:new Map((o=o.sort((t,s)=>(function(e,t,s){if(t.server!==s.server){let n=sx(e,t),r=sx(e,s);return n&&r&&n!==r?n.localeCompare(r):t.server>s.server?1:-1}if(sI(t)!==sI(s))return sI(s)?1:-1;if(t.type===sh&&s.type===sh){let e=sN(t.name,t.name[0]),n=sN(s.name,s.name[0]),r=e.localeCompare(n);if(0!==r)return r}return t.name.localeCompare(s.name)})(e,t,s))).map(e=>[e.id,e]))}]},storeBouncerNetwork(e,t,s){let n=new Map(e.bouncerNetworks);return n.set(t,{...n.get(t),...s}),{bouncerNetworks:n}},deleteBouncerNetwork(e,t){let s=new Map(e.bouncerNetworks);return s.delete(t),{bouncerNetworks:s}},handleMessage(e,t,s,n){let r,a,i,o,l;function u(t){return sO.updateServer(e,s,t)}function c(t,n){return sO.updateBuffer(e,{server:s,name:t},n)}function h(e,t){return u(s=>{let n=new tq(s.users),r=s_(n.get(e),t);if(r)return n.set(e,r),{users:n}})}if(!tV(t,"chathistory"))switch(t.command){case"004":return c("*",{serverInfo:{name:t.params[1],version:t.params[2]}});case"005":return l=new Map(e.buffers),e.buffers.forEach(e=>{if(e.server!==s)return;let t=new tq(e.members,n.cm);l.set(e.id,{...e,members:t})}),{buffers:l,...u(e=>({name:n.isupport.network(),cm:n.cm,users:new tq(e.users,n.cm),reliableUserAccounts:n.isupport.monitor()>0&&n.isupport.whox(),statusMsg:n.isupport.statusMsg(),membershipModes:n.isupport.membershipModes(),bouncerNetID:n.isupport.bouncerNetID()}))};case"CAP":return u({supportsSASLPlain:n.supportsSASL("PLAIN"),supportsAccountRegistration:n.caps.enabled.has("draft/account-registration"),isBouncer:n.caps.enabled.has("soju.im/bouncer-networks")});case"900":return u({account:t.params[2]});case"901":return u({account:null});case"REGISTER":case"VERIFY":if("SUCCESS"===t.params[0])return u({account:t.params[1]});break;case"331":return c(a=t.params[1],{topic:null});case"332":return c(a=t.params[1],{topic:t.params[2]});case"333":break;case"366":a=t.params[1];let p=n.isupport.membershipModes().map(({prefix:e})=>e);return c(a,e=>{let s=new tq(null,e.members.caseMap);return t.list.forEach(e=>{e.params[3].split(" ").forEach(e=>{let t=tP(e,p);s.set(t.name,t.prefix)})}),{members:s}});case"315":if(r=t.params[1],0===t.list.length&&!n.isChannel(r)&&0>r.indexOf("*"))return h(r,{offline:!0});return u(e=>{let s=new tq(e.users);for(let e of t.list){let t=n.parseWhoReply(e);if(void 0!==t.flags){t.away=t.flags.indexOf("G")>=0,t.operator=t.flags.indexOf("*")>=0;let e=n.isupport.bot();e&&(t.bot=t.flags.indexOf(e)>=0),delete t.flags}t.offline=!1,s.set(t.nick,t)}return{users:s}});case"JOIN":if(a=t.params[0],n.isMyNick(t.prefix.name)){let[t,r]=sO.createBuffer(e,a,s,n);e={...e,...r}}return o=c(a,e=>{let s=new tq(e.members);return s.set(t.prefix.name,""),{members:s,joined:e.joined||n.isMyNick(t.prefix.name)}}),e={...e,...o},i={nick:t.prefix.name,offline:!1},t.prefix.user&&(i.username=t.prefix.user),t.prefix.host&&(i.hostname=t.prefix.host),t.params.length>2&&(i.account=t.params[1],"*"===i.account&&(i.account=null),i.realname=t.params[2]),o=h(t.prefix.name,i),e={...e,...o};case"PART":return c(a=t.params[0],e=>{let s=new tq(e.members);return s.delete(t.prefix.name),{members:s,joined:e.joined&&!n.isMyNick(t.prefix.name)}});case"KICK":a=t.params[0];let d=t.params[1];return c(a,e=>{let t=new tq(e.members);return t.delete(d),{members:t,joined:e.joined&&!n.isMyNick(d)}});case"QUIT":return l=new Map(e.buffers),e.buffers.forEach(e=>{if(e.server!==s||!e.members.has(t.prefix.name))return;let n=new tq(e.members);n.delete(t.prefix.name),l.set(e.id,{...e,members:n})}),e={...e,buffers:l},o=h(t.prefix.name,e=>{if(e)return{offline:!0}}),e={...e,...o};case"NICK":let m=t.params[0];return l=new Map(e.buffers),e.buffers.forEach(e=>{if(e.server!==s)return;let n=e.members.get(t.prefix.name);if(void 0===n)return;let r=new tq(e.members);r.set(m,n),r.delete(t.prefix.name),l.set(e.id,{...e,members:r})}),e={...e,buffers:l},o=u(e=>{let s=new tq(e.users),n=s.get(t.prefix.name);if(n)return s.set(m,n),s.delete(t.prefix.name),{users:s}}),e={...e,...o};case"SETNAME":return h(t.prefix.name,{realname:t.params[0]});case"CHGHOST":return h(t.prefix.name,{username:t.params[0],hostname:t.params[1]});case"ACCOUNT":let f=t.params[0];return"*"===f&&(f=null),h(t.prefix.name,{account:f});case"AWAY":let g=t.params[0];return h(t.prefix.name,{away:!!g});case"TOPIC":return c(a=t.params[0],{topic:t.params[1]});case"MODE":if(r=t.params[0],!n.isChannel(r))return;let b=new Map(n.isupport.membershipModes().map(e=>[e.mode,e.prefix]));return c(r,e=>{let s=new tq(e.members);return!function(e,t,s){let[n,r,a,i]=t.chanModes(),o=t.membershipModes(),l=new Map;if(Array.from(n).forEach(e=>l.set(e,"A")),Array.from(r).forEach(e=>l.set(e,"B")),Array.from(a).forEach(e=>l.set(e,"C")),Array.from(i).forEach(e=>l.set(e,"D")),o.forEach(e=>l.set(e.mode,"B")),"MODE"!==e.command)throw Error("Expected a MODE message");let u=e.params[1],c=e.params.slice(2),h=null,p=0;for(let e=0;e<u.length;e++){if("+"===u[e]||"-"===u[e]){h=u[e];continue}if(!h)throw Error("malformed mode string: missing plus/minus");let t=u[e],n="+"===h,r=l.get(t);if(!r)continue;let a=null;("A"===r||"B"===r||"C"===r&&n)&&(a=c[p],p++),s(t,n,a)}}(t,n.isupport,(e,t,r)=>{let a=b.get(e);if(void 0===a)return;if(!r)throw Error(`Missing membership MODE "${e}" argument`);let i=s.get(r);if(void 0!==i){var o;let e;s.set(r,(o=i,e=new Map(n.isupport.membershipModes().map((e,t)=>[e.prefix,t])),t?0>o.indexOf(a)&&(o+=a,o=Array.from(o).sort((t,s)=>e.get(t)-e.get(s)).join("")):o=o.replace(a,""),o))}}),{members:s}});case"REDACT":return r=t.params[0],n.isMyNick(r)&&(r=t.prefix.name),c(r,e=>({redacted:new Set(e.redacted).add(t.params[1])}));case"730":case"731":for(let s of t.params[1].split(",")){let n=h(tA(s).name,{offline:"731"===t.command});e={...e,...n}}return e}},addMessage:(e,t,s)=>(t.key=++sD,sO.updateBuffer(e,s,e=>({messages:function(e,t){if(0===e.length)return[t];if(!tV(t,"chathistory")||e[e.length-1].tags.time<=t.tags.time)return e.concat(t);let s=-1;for(let n=0;n<e.length;n++){let r=e[n];if(t.tags.time<r.tags.time){s=n;break}}return console.assert(s>=0,""),(e=[...e]).splice(s,0,t),e}(e.messages,t)})))};function sA(e,t){let s=null;return(...n)=>{clearTimeout(s),s=setTimeout(()=>{s=null,e(...n)},t)}}class sL{constructor(e){this.k="gamja_"+e}load(){let e=localStorage.getItem(this.k);return e?JSON.parse(e):null}put(e){e?localStorage.setItem(this.k,JSON.stringify(e)):localStorage.removeItem(this.k)}}let sM=new sL("autoconnect"),sP=new sL("naggedProtocolHandler"),sB=new sL("settings");class sH{raw=new sL("buffers");m=null;constructor(){let e=this.raw.load();this.m=new Map(Object.entries(e||{}));let t=this.save.bind(this);this.save=sA(t,500),document.addEventListener("visibilitychange",()=>{"hidden"===document.visibilityState&&t()})}key(e){return JSON.stringify({name:e.name.toLowerCase(),server:{bouncerNetwork:e.server.bouncerNetwork}})}save(){this.m.size>0?this.raw.put(Object.fromEntries(this.m)):this.raw.put(null)}get(e){return this.m.get(this.key(e))}put(e){let t=this.key(e),s=!this.m.has(t),n=this.m.get(t)||{},r=n.unread||sm.NONE;void 0!==e.unread&&e.unread!==n.unread&&(r=e.unread,s=!0);let a={...n.receipts};e.receipts&&(Object.keys(e.receipts).forEach(t=>{(!a[t]||a[t].time<e.receipts[t].time)&&(a[t]=e.receipts[t],s=!0)}),a[sf]<a[sg]&&(a[sf]=a[sg],s=!0));let i=n.closed||!1;return void 0!==e.closed&&e.closed!==n.closed&&(i=e.closed,s=!0),!!s&&(this.m.set(this.key(e),{name:e.name,unread:r,receipts:a,closed:i,server:{bouncerNetwork:e.server.bouncerNetwork}}),this.save(),!0)}delete(e){this.m.delete(this.key(e)),this.save()}list(e){let t=new Set,s=[];for(let n of this.m.values())n.server.bouncerNetwork===e.bouncerNetwork&&(t.has(n.name)||(s.push(n),t.add(n.name)));return s}clear(e){if(e)for(let t of this.list(e))this.m.delete(this.key(t));else this.m=new Map;this.save()}}function sj(e){if(!this.props.value)return null;let t=tx[this.props.value[0]]||"";return tI`
		<span class="membership ${t}" title=${t}>
			${this.props.value}
		</span>
	`}function sU(e){let t;e.user&&tQ(e.user.realname,e.nick)&&(t=su(e.user.realname));let s=tY({host:e.bouncerNetwork?.host,entity:e.nick,enttype:"user"}),n=function(e){let t=5381;for(let s=0;s<e.length;s++)t=(t<<5)+t+e.charCodeAt(s)>>>0;return t}(e.nick)%16+1;return tI`
		<a
			href=${s}
			title=${t}
			class="nick nick-${n}"
			onClick=${function(t){t.preventDefault(),e.onClick()}}
		>${e.nick}</a>
	`}function sW({date:e,url:t,showSeconds:s}){if(!e){let e="--:--";return s&&(e+=":--"),tI`<span class="timestamp" aria-hidden="true">${e}</span>`}let n=e.getHours().toString().padStart(2,"0"),r=e.getMinutes().toString().padStart(2,"0"),a=`${n}:${r}`;return s&&(a+=":"+e.getSeconds().toString().padStart(2,"0")),tI`
		<a
			href=${t}
			class="timestamp"
			title=${e.toLocaleString()}
			onClick=${e=>e.preventDefault()}
		>
			${a}
		</a>
	`}function sF(e){return tI`
		<${sv.Consumer}>
			${t=>tI`
				<${sW} ...${e} showSeconds=${t.secondsInTimestamps}/>
			`}
		</>
	`}function sK(e){switch(e.command){case"JOIN":case"PART":case"QUIT":case"NICK":return!0}return!1}class sG extends R{shouldComponentUpdate(e){return this.props.message!==e.message||this.props.redacted!==e.redacted}render(){let e,t,s,n,r=this.props.message,a=this.props.buffer,i=this.props.server,o=this.props.bouncerNetwork,l=this.props.onNickClick,u=this.props.onChannelClick,c=this.props.onVerifyClick;function h(e){return tI`
				<${sU}
					nick=${e}
					user=${i.users.get(e)}
					bouncerNetwork=${o}
					onClick=${()=>l(e)}
				/>
			`}let p="";switch(r.command){case"NOTICE":case"PRIVMSG":s=r.params[0];let d=r.params[1],m=tF(r);if(m)"ACTION"===m.command?(p="me-tell",e=tI`* ${h(r.prefix.name)} ${si(su(m.param),u)}`):e=tI`
						${h(r.prefix.name)} has sent a CTCP command: ${m.command} ${m.param}
					`;else{let t="<",s=">";"NOTICE"===r.command&&(p+=" notice",t=s="-"),this.props.redacted?e=tI`<i>This message has been deleted.</i>`:(e=tI`${si(su(d),u)}`,p+=" talk"),e=tI`
					<span class="nick-caret" aria-hidden="true">${t}</span>
					${h(r.prefix.name)}
					<span class="nick-caret" aria-hidden="true">${s}</span>
					${" "}
					${e}
				`}let f=i.statusMsg;if(s!==a.name&&f){let t=tP(s,f);t.name===a.name&&(e=[tI`(<${sj} value=${t.prefix}/>)`," ",e])}r.tags["+draft/channel-context"]&&(e=tI`<em>(only visible to you)</em> ${e}`),r.isHighlight&&(p+=" highlight");break;case"JOIN":e=tI`
				${h(r.prefix.name)} has joined
			`;break;case"PART":e=tI`
				${h(r.prefix.name)} has left
			`;break;case"QUIT":e=tI`
				${h(r.prefix.name)} has quit
			`;break;case"NICK":let g=r.params[0];e=tI`
				${h(r.prefix.name)} is now known as ${h(g)}
			`;break;case"KICK":e=tI`
				${h(r.params[1])} was kicked by ${h(r.prefix.name)} (${r.params.slice(2)})
			`;break;case"MODE":s=r.params[0];let b=r.params[1],k=tI`${h(r.prefix.name)}`;if(a.type===sh&&2===b.length&&i.cm(a.name)===i.cm(s)){let t,s,n=b[0],a=b[1],o=r.params[2];switch(a){case"b":t="+"===n?"added":"removed",e=tI`${k} has ${t} a ban on ${o}`;break;case"e":t="+"===n?"added":"removed",e=tI`${k} has ${t} a ban exemption on ${o}`;break;case"l":e="+"===n?tI`${k} has set the channel user limit to ${o}`:tI`${k} has unset the channel user limit`;break;case"i":t="+"===n?"marked":"unmarked",e=tI`${k} has ${t} as invite-only`;break;case"m":t="+"===n?"marked":"unmarked",e=tI`${k} has ${t} as moderated`;break;case"s":t="+"===n?"marked":"unmarked",e=tI`${k} has ${t} as secret`;break;case"t":t="+"===n?"locked":"unlocked",e=tI`${k} has ${t} the channel topic`;break;case"n":t="+"===n?"allowed":"denied",e=tI`${k} has ${t} external messages to this channel`}if(e)break;for(let e of i.membershipModes)if(e.mode===a){s=tx[e.prefix];break}if(s&&o){e=tI`
						${k} has ${"+"===n?"granted":"revoked"} ${s} privileges ${"+"===n?"to":"from"} ${h(o)}
					`;break}}e=tI`
				${k} sets mode ${r.params.slice(1).join(" ")}
			`,i.cm(a.name)!==i.cm(s)&&(e=tI`${e} on ${s}`);break;case"TOPIC":let v=r.params[1];e=v?tI`
					${h(r.prefix.name)} changed the topic to: ${si(su(v),u)}
				`:tI`
					${h(r.prefix.name)} cleared the topic
				`;break;case"INVITE":t=r.params[0];let w=r.params[1];if(a.type===sc){let t;p="talk",e=tI`
					You have been invited to ${t=tY({host:o?.host,entity:w}),tI`
				<a href=${t} onClick=${u}>
					${w}
				</a>
			`} by ${h(r.prefix.name)}
				`}else e=tI`
					${h(r.prefix.name)} has invited ${h(t)} to the channel
				`;break;case"001":let y=r.params[0];e=tI`Connected to server, your nickname is ${y}`;break;case"341":t=r.params[1],e=tI`${h(t)} has been invited to the channel`;break;case"372":p="motd",e=si(su(r.params[1]),u);break;case"900":n=r.params[2],e=tI`You are now authenticated as ${n}`;break;case"901":e=tI`You are now unauthenticated`;break;case"REGISTER":n=r.params[1];let C=r.params[2];switch(r.params[0]){case"SUCCESS":e=tI`A new account has been created, you are now authenticated as ${n}`;break;case"VERIFICATION_REQUIRED":e=tI`A new account has been created, but you need to <a href="#" onClick=${function(e){e.preventDefault(),c(n,C)}}>verify it</a>: ${si(C,u)}`}break;case"VERIFY":n=r.params[1],e=tI`The new account has been verified, you are now authenticated as ${n}`;break;case"221":let S=r.params[1];e=S?tI`Your user mode is ${S}`:tI`You have no user mode`;break;case"324":e=tI`Channel mode is ${r.params.slice(2).join(" ")}`;break;case"329":let E=new Date(1e3*parseInt(r.params[2],10));e=tI`Channel was created on ${E.toLocaleString()}`;break;case"730":e=tI`${h(a.name)} is online`;break;case"731":e=tI`${h(a.name)} is offline`;break;default:tU(r.command)&&"422"!==r.command&&(p="error"),e=tI`${r.command} ${si(r.params.join(" "),u)}`}return e?tI`
			<div class="logline ${p}" data-key=${r.key} role="listitem">
				<${sF} date=${new Date(r.tags.time)} url=${sy(a,r,o)}/>
				${" "}
				${e}
			</div>
		`:null}}class sz extends R{shouldComponentUpdate(e){return this.props.messages[0]!==e.messages[0]||this.props.messages[this.props.messages.length-1]!==e.messages[e.messages.length-1]}render(){let e=this.props.messages,t=this.props.buffer,s=this.props.server,n=this.props.bouncerNetwork,r=this.props.onNickClick;function a(e){return tI`
				<${sU}
					nick=${e}
					user=${s.users.get(e)}
					bouncerNetwork=${n}
					onClick=${()=>r(e)}
				/>
			`}let i={JOIN:[],PART:[],QUIT:[],NICK:[]};e.forEach(e=>{i[e.command].push(e)});let o=!0,l=[];["JOIN","PART","QUIT"].forEach(e=>{let t;if(0===i[e].length)return;let s=new Set(i[e].map(e=>e.prefix.name)),n=s.size>1;switch(e){case"JOIN":t=n?"have joined":"has joined";break;case"PART":t=n?"have left":"has left";break;case"QUIT":t=n?"have quit":"has quit"}o?o=!1:l.push(", "),l.push(function(e,t){if(0===e.length)return null;if(1===e.length)return t(e[0]);let s=e.slice(0,e.length-1).map((e,s)=>0===s?t(e):[", ",t(e)]);return s.push(" and "),s.push(t(e[e.length-1])),s}([...s],a)),l.push(" "+t)}),i.NICK.forEach(e=>{o?o=!1:l.push(", ");let t=e.params[0];l.push(tI`
				${a(e.prefix.name)} is now known as ${a(t)}
			`)});let u=e[e.length-1],c=new Date(e[0].tags.time),h=new Date(u.tags.time),p=tI`
			<${sF} date=${c} url=${sy(t,e[0],n)}/>
		`;return h-c>6e3&&(p=[p," — ",tI`
					<${sF} date=${h} url=${sy(t,u,n)}/>
				`]),tI`
			<div class="logline" data-key=${e[0].key} role="listitem">
				${p}
				${" "}
				${l}
			</div>
		`}}let sq=!1;if(window.Notification&&(sq=!0,"default"===Notification.permission))try{new Notification("")}catch(e){"TypeError"===e.name&&(sq=!1)}class sV extends R{state={nag:!1};constructor(e){super(e),this.handleClick=this.handleClick.bind(this),this.state.nag=this.shouldNag()}shouldNag(){return sq&&"default"===Notification.permission}handleClick(e){e.preventDefault(),Notification.requestPermission(e=>{this.setState({nag:this.shouldNag()})})}render(){return this.state.nag?tI`
			<div class="logline nag" role="listitem">
				<${sF}/>
				${" "}
				<a href="#" onClick=${this.handleClick}>Turn on desktop notifications</a> to get notified about new messages
			</div>
		`:null}}class sQ extends R{state={nag:!0};constructor(e){super(e),this.handleClick=this.handleClick.bind(this),this.state.nag=!sP.load()}handleClick(e){e.preventDefault();let t=window.location.origin+window.location.pathname+"?open=%s";try{navigator.registerProtocolHandler("irc",t),navigator.registerProtocolHandler("ircs",t)}catch(e){console.error("Failed to register protocol handler: ",e)}sP.put(!0),this.setState({nag:!1})}render(){if(!navigator.registerProtocolHandler||!this.state.nag)return null;let e=this.props.bouncerName||"this bouncer";return tI`
			<div class="logline nag" role="listitem">
				<${sF}/>
				${" "}
				<a href="#" onClick=${this.handleClick}>Register our protocol handler</a> to open IRC links with ${e}
			</div>
		`}}function sJ({server:e,onAuthClick:t,onRegisterClick:s}){let n="an account on this server";e.name&&(n="a "+e.name+" account");let r=[tI`
		You are unauthenticated on this server,
		${" "}
		<a href="#" onClick=${function(e){e.preventDefault(),t()}}>login</a>
		${" "}
	`];return e.supportsAccountRegistration?r.push(tI`or <a href="#" onClick=${function(e){e.preventDefault(),s()}}>register</a> ${n}`):r.push(tI`if you have ${n}`),tI`
		<div class="logline nag" role="listitem">
			<${sF}/> ${r}
		</div>
	`}class sY extends R{constructor(e){super(e)}shouldComponentUpdate(e){return this.props.date.getTime()!==e.date.getTime()}render(){let e=this.props.date.toLocaleDateString([],{year:"numeric",month:"2-digit",day:"2-digit"});return tI`
			<div class="separator date-separator" role="separator">
				${e}
			</div>
		`}}function sX(e){return tI`<div class="separator unread-separator" role="separator">New messages</div>`}class sZ extends R{shouldComponentUpdate(e){return this.props.buffer!==e.buffer||this.props.settings!==e.settings}render(){let e=this.props.buffer;if(!e)return null;let t=this.props.server,s=this.props.bouncerNetwork,n=this.props.settings,r=t.name,a=[];e.type===sc&&a.push(tI`<${sV}/>`),e.type===sc&&t.isBouncer&&!t.bouncerNetID&&a.push(tI`<${sQ} bouncerName=${r}/>`),e.type===sc&&t.status===sd.REGISTERED&&t.supportsSASLPlain&&!t.account&&a.push(tI`
				<${sJ}
					server=${t}
					onAuthClick=${this.props.onAuthClick}
					onRegisterClick=${this.props.onRegisterClick}
				/>
			`);let i=this.props.onChannelClick,o=this.props.onNickClick,l=this.props.onVerifyClick;function u(n){return tI`
				<${sG}
					key=${"msg-"+n.key}
					message=${n}
					buffer=${e}
					server=${t}
					bouncerNetwork=${s}
					redacted=${e.redacted.has(n.tags.msgid)}
					onChannelClick=${i}
					onNickClick=${o}
					onVerifyClick=${l}
				/>
			`}function c(n){let r=new Map,a=[];for(let e of n){let t=!0;switch(e.command){case"PART":case"QUIT":r.delete(e.prefix.name);break;case"NICK":let s=r.get(e.prefix.name);if(!s){e={...e},r.set(e.params[0],e);break}s.params=e.params,r.delete(e.prefix.name),r.set(e.params[0],s),t=!1}t&&a.push(e)}n=a;let i=new Map,l=[];return(n.forEach((e,t)=>{("PART"===e.command||"QUIT"===e.command)&&i.set(e.prefix.name,t),"JOIN"===e.command&&i.has(e.prefix.name)?(l[i.get(e.prefix.name)]=!1,i.delete(e.prefix.name),l.push(!1)):"NICK"===e.command&&e.prefix.name===e.params[0]?l.push(!1):l.push(!0)}),0===(n=n.filter((e,t)=>l[t])).length)?null:1===n.length?u(n[0]):tI`
				<${sz}
					key=${"fold-"+n[0].key+"-"+n[n.length-1].key}
					messages=${n}
					buffer=${e}
					server=${t}
					bouncerNetwork=${s}
					onNickClick=${o}
				/>
			`}let h=!1,p=new Date,d=[],m=null;return e.messages.forEach(t=>{var s;let r=[];if(n.bufferEvents===sk&&sK(t))return;if("730"===t.command||"731"===t.command){let e=!m||t.command===m;if(m=t.command,e)return}h||e.type===sc||s$(t,e.prevReadReceipt)||(r.push(tI`<${sX} key="unread"/>`),h=!0);let i=new Date(t.tags.time);(((s=p).getFullYear()!==i.getFullYear()||s.getMonth()!==i.getMonth()||s.getDate()!==i.getDate())&&r.push(tI`<${sY} key=${"date-"+i} date=${i}/>`),p=i,r.length>0&&(a.push(c(d)),a.push(...r),d=[]),n.bufferEvents===sb&&sK(t))?d.push(t):(d.length>0&&(a.push(c(d)),d=[]),a.push(u(t)))}),a.push(c(d)),tI`
			<div class="logline-list" role="list">
				${a}
			</div>
		`}}function s0(e){let t,s=e.buffer.name;e.buffer.type===sc&&(s=sC(e.server,e.bouncerNetwork));let n=["type-"+e.buffer.type];switch(e.active&&n.push("active"),e.buffer.unread!==sm.NONE&&n.push("unread-"+e.buffer.unread),e.buffer.type){case sc:let r=e.server.status===sd.DISCONNECTED;e.bouncerNetwork&&e.bouncerNetwork.error&&(r=!0),r&&n.push("error");break;case sp:let a=e.server.users.get(s);a&&tQ(a.realname,s)&&(t=su(a.realname))}return tI`
		<li class="${n.join(" ")}" role="tab" aria-selected="${e.active}">
			<a
				href=${sw(e.buffer,e.bouncerNetwork)}
				title=${t}
				onClick=${function(t){t.preventDefault(),e.onClick()}}
				onMouseDown=${function(t){1===t.button&&(t.preventDefault(),e.onClose())}}
			>${s}</a>
		</li>
	`}function s1(e){let t=Array.from(e.buffers.values()).map(t=>{let s=e.servers.get(t.server),n=null;return s.bouncerNetID&&(n=e.bouncerNetworks.get(s.bouncerNetID)),tI`
			<${s0}
				key=${t.id}
				buffer=${t}
				server=${s}
				bouncerNetwork=${n}
				onClick=${()=>e.onBufferClick(t)}
				onClose=${()=>e.onBufferClose(t)}
				active=${e.activeBuffer===t.id}
			/>
		`});return tI`
		<ul role="tablist" aria-label="Buffer list">
			${t}
		</ul>
	`}let s2="here",s3="gone",s4="offline";function s5(e){let t={[s2]:"User is online",[s3]:"User is away",[s4]:"User is offline"}[e.status];return tI`<span class="status status-${e.status}" title=${t}>●</span>`}function s6(e){let t=e.server.status===sd.REGISTERED;e.bouncerNetwork&&(t=t&&"connected"===e.bouncerNetwork.state);let s=null,n=[];switch(e.buffer.type){case sc:switch(e.server.status){case sd.DISCONNECTED:s="Disconnected";break;case sd.CONNECTING:s="Connecting...";break;case sd.REGISTERING:s="Logging in...";break;case sd.REGISTERED:if(e.bouncerNetwork)switch(e.bouncerNetwork.state){case"disconnected":s="Bouncer disconnected from network",e.bouncerNetwork.error&&(s+=": "+e.bouncerNetwork.error);break;case"connecting":s="Bouncer connecting to network...";break;case"connected":s=`Connected to ${e.bouncerNetwork.host||"network"}`}else if(e.buffer.serverInfo){let t=e.buffer.serverInfo;s=`Connected to ${t.name}`}else s="Connected"}let r=tI`
			<button
				key="join"
				onClick=${e.onJoin}
			>Join channel</button>
		`,a=tI`
			<button
				key="reconect"
				onClick=${e.onReconnect}
			>Reconnect</button>
		`,i=tI`
			<button
				key="settings"
				onClick="${e.onOpenSettings}"
			>Settings</button>
		`;e.server.isBouncer?e.server.bouncerNetID?(t&&n.push(r),e.server.status===sd.REGISTERED&&n.push(tI`
						<button
							key="manage"
							onClick=${e.onManageNetwork}
						>Manage network</button>
					`)):(t?n.push(tI`
						<button
							key="add"
							onClick=${e.onAddNetwork}
						>Add network</button>
					`):e.server.status===sd.DISCONNECTED&&n.push(a),n.push(i)):(t?n.push(r):e.server.status===sd.DISCONNECTED&&n.push(a),n.push(i));break;case sh:e.buffer.topic&&(s=si(su(e.buffer.topic),e.onChannelClick)),e.buffer.joined?n.push(tI`
				<button
					key="part"
					class="danger"
					onClick=${e.onClose}
				>Leave</button>
			`):(t&&n.push(tI`
					<button
						key="join"
						onClick=${e.onJoin}
					>Join</button>
				`),n.push(tI`
				<button
					key="part"
					class="danger"
					onClick=${e.onClose}
				>Close</button>
			`));break;case sp:if(e.user){let t=s2;e.user.offline?t=s4:e.user.away&&(t=s3);let n=e.buffer.name;tQ(e.user.realname,e.buffer.name)&&(n=su(e.user.realname||""));let r=[];if(e.user.username&&e.user.hostname&&r.push(`${e.user.username}@${e.user.hostname}`),e.user.account){let t,s=`This user is verified and has logged in to the server with the account ${e.user.account}.`;t=e.user.account===e.buffer.name?"authenticated":`authenticated as ${e.user.account}`,r.push(tI`<abbr title=${s}>${t}</abbr>`)}else e.server.reliableUserAccounts&&r.push(tI`<abbr title=${"This user has not been verified and is not logged in."}>unauthenticated</abbr>`);e.user.operator&&r.push(tI`<abbr title=${"This user is a server operator, they have administrator privileges."}>server operator</abbr>`),e.user.bot&&r.push(tI`<abbr title=${"This user is an automated bot."}>bot</abbr>`),(r=r.map((e,t)=>0===t?e:[", ",e])).length>0&&(r=["(",r,")"]),s=tI`<${s5} status=${t}/> ${n} ${r}`}n=tI`
			<button
				key="close"
				class="danger"
				onClick=${e.onClose}
			>Close</button>
		`}let o=e.buffer.name;return e.buffer.type===sc&&(o=sC(e.server,e.bouncerNetwork)),tI`
		<div class="title">${o}</div>
		${s?tI`<div class="description">${s}</div>`:null}
		<div class="actions">${n}</div>
	`}class s7 extends R{constructor(e){super(e),this.handleClick=this.handleClick.bind(this)}shouldComponentUpdate(e){return this.props.nick!==e.nick||this.props.membership!==e.membership||this.props.user!==e.user}handleClick(e){e.preventDefault(),this.props.onClick()}render(){let e,t=this.props.user,s=["nick"];if(t){let n="";t.username&&t.hostname&&(n=`${t.username}@${t.hostname}`),tQ(t.realname,this.props.nick)?(e=su(t.realname),n&&(e=`${e} (${n})`)):e=n,t.account&&(e+=`
Authenticated as ${t.account}`),t.away&&(s.push("away"),e+="\nAway")}let n=tY({host:this.props.bouncerNetwork?.host,entity:this.props.nick,enttype:"user"});return tI`
			<li>
				<a
					href=${n}
					class=${s.join(" ")}
					title=${e}
					onClick=${this.handleClick}
				>
					<${sj} value=${this.props.membership}/>
					${this.props.nick}
				</a>
			</li>
		`}}function s9(e,t){let[s,n]=e,[r,a]=t,i=["~","&","@","%","+"],o=i.indexOf(n[0]),l=i.indexOf(a[0]);return(o<0&&(o=i.length),l<0&&(l=i.length),o!==l)?o-l:s.localeCompare(r)}class s8 extends R{shouldComponentUpdate(e){return this.props.members!==e.members||this.props.users!==e.users}render(){return tI`
			<ul>
				${Array.from(this.props.members).sort(s9).map(([e,t])=>tI`
					<${s7}
						key=${e}
						nick=${e}
						membership=${t}
						user=${this.props.users.get(e)}
						bouncerNetwork=${this.props.bouncerNetwork}
						onClick=${()=>this.props.onNickClick(e)}
					/>
				`)}
			</ul>
		`}}class ne extends R{constructor(e){super(e),this.handleSubmit=this.handleSubmit.bind(this)}handleSubmit(e){e.preventDefault(),this.props.onSubmit()}render(){let e=this.props.client.isChannel(this.props.name),t=null;return this.props.bouncerNetwork&&(t=` on ${sC(this.props.server,this.props.bouncerNetwork)}`),tI`
			<form onSubmit=${this.handleSubmit}>
				<p>
					Do you want to open a new buffer for ${e?"channel":"user"} <strong>${this.props.name}</strong>${t}?
				</p>
				<button>Open</button>
			</form>
		`}}class nt extends R{state={url:"",pass:"",nick:"",password:"",rememberMe:!1,username:"",realname:"",autojoin:!0};nickInput=x();constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),e.params&&(this.state={...this.state,url:e.params.url||"",nick:e.params.nick||"",rememberMe:e.params.autoconnect||!1,username:e.params.username||"",realname:e.params.realname||""})}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){if(e.preventDefault(),this.props.connecting)return;let t={url:this.state.url,pass:this.state.pass,nick:this.state.nick,autoconnect:this.state.rememberMe,username:this.state.username,realname:this.state.realname,saslPlain:null,autojoin:[]};this.state.password?t.saslPlain={username:t.username||t.nick,password:this.state.password}:"external"===this.props.auth?t.saslExternal=!0:"oauth2"===this.props.auth&&(t.saslOauthBearer=this.props.params.saslOauthBearer),this.state.autojoin&&(t.autojoin=this.props.params.autojoin||[]),this.props.onSubmit(t)}componentDidMount(){this.nickInput.current&&this.nickInput.current.focus()}render(){let e=this.props.connecting,t=null;this.props.params&&this.props.params.url||(t=tI`
				<label>
					Server URL:<br/>
					<input
						type="text"
						name="url"
						value=${this.state.url}
						disabled=${e}
						inputmode="url"
					/>
				</label>
				<br/><br/>
			`);let s=null;this.props.connecting?s=tI`
				<p>Connecting...</p>
			`:this.props.error&&(s=tI`
				<p class="error-text">${si(this.props.error)}</p>
			`);let n=null;"disabled"!==this.props.auth&&"external"!==this.props.auth&&"oauth2"!==this.props.auth&&(n=tI`
				<label>
					Password:<br/>
					<input
						type="password"
						name="password"
						value=${this.state.password}
						disabled=${e}
						required=${"mandatory"===this.props.auth}
						placeholder=${"mandatory"!==this.props.auth?"(optional)":""}
					/>
				</label>
				<br/><br/>
			`);let r=null,a=this.props.params.autojoin||[];if(a.length>0){let e=a.length>1?"s":"";r=tI`
				<label>
					<input
						type="checkbox"
						name="autojoin"
						checked=${this.state.autojoin}
					/>
					Auto-join channel${e} <strong>${a.join(", ")}</strong>
				</label>
				<br/><br/>
			`}return tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<h2>Connect to IRC</h2>

				<label>
					Nickname:<br/>
					<input
						type="username"
						name="nick"
						value=${this.state.nick}
						disabled=${e}
						ref=${this.nickInput}
						required
						autofocus
					/>
				</label>
				<br/><br/>

				${n}

				${r}

				<label>
					<input
						type="checkbox"
						name="rememberMe"
						checked=${this.state.rememberMe}
						disabled=${e}
					/>
					Remember me
				</label>
				<br/><br/>

				<details>
					<summary role="button">Advanced options</summary>

					<br/>

					${t}

					<label>
						Username:<br/>
						<input
							type="username"
							name="username"
							value=${this.state.username}
							disabled=${e}
							placeholder="Same as nickname"
						/>
					</label>
					<br/><br/>

					<label>
						Real name:<br/>
						<input
							type="text"
							name="realname"
							value=${this.state.realname}
							disabled=${e}
							placeholder="Same as nickname"
						/>
					</label>
					<br/><br/>

					<label>
						Server password:<br/>
						<input
							type="password"
							name="pass"
							value=${this.state.pass}
							disabled=${e}
							placeholder="None"
						/>
					</label>
					<br/><br/>
				</details>

				<br/>
				<button disabled=${e}>Connect</button>

				${s}
			</form>
		`}}class ns extends R{state={channel:"#"};constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),e.channel&&(this.state.channel=e.channel)}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){e.preventDefault();let t={channel:this.state.channel};this.props.onSubmit(t)}render(){return tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<label>
					Channel:<br/>
					<input type="text" name="channel" value=${this.state.channel} autofocus required/>
				</label>
				<br/>

				<br/>
				<button>Join</button>
			</form>
		`}}function nn(e,t,s){let n=Array.from(e.values()),r=n.findIndex(e=>e.id===t);return r<0?null:(r=(r+n.length+s)%n.length,n[r])}let nr=[{key:"h",altKey:!0,description:"Mark all messages as read",execute:e=>{e.setState(t=>{let s=new Map;return t.buffers.forEach(t=>{s.set(t.id,{...t,unread:sm.NONE,prevReadReceipt:null});let n={};if(t.messages.length>0){let e=t.messages[t.messages.length-1];n[sg]=sS(e)}let r=e.clients.get(t.server);e.bufferStore.put({name:t.name,server:r.params,unread:sm.NONE,receipts:n})}),{buffers:s}},()=>{e.updateDocumentTitle()})}},{key:"a",altKey:!0,description:"Jump to next buffer with activity",execute:e=>{let t=null,s=null;for(let n of e.state.buffers.values())t||n.type!==sc||(t=n),n.unread!==sm.NONE&&(!s||sm.compare(n.unread,s.unread)>0)&&(s=n);s||(s=t),s&&e.switchBuffer(s)}},{key:"ArrowUp",altKey:!0,description:"Jump to the previous buffer",execute:e=>{let t=nn(e.state.buffers,e.state.activeBuffer,-1);t&&e.switchBuffer(t)}},{key:"ArrowDown",altKey:!0,description:"Jump to the next buffer",execute:e=>{let t=nn(e.state.buffers,e.state.activeBuffer,1);t&&e.switchBuffer(t)}},{key:"k",ctrlKey:!0,description:"Switch to a buffer",execute:e=>{e.openDialog("switch")}}];function na(e){let t=e.state.buffers.get(e.state.activeBuffer);if(!t)throw Error("Not connected to server");return e.clients.get(t.server)}function ni(e){let t=e.state.buffers.get(e.state.activeBuffer);if(!t||t.type!==sh)throw Error("Not in a channel");return t.name}async function no(e,t,s){let n=t[0];if(!n)throw Error("Missing nick");let r=ni(e),a=na(e),i=(await a.whois(n))["311"].params,o=i[2],l=i[3];a.send({command:"MODE",params:[r,s,`*!${o}@${l}`]})}function nl(e){let t=e.state.buffers.get(e.state.activeBuffer);t&&t.type!==sc&&e.setBufferState({server:t.server},e=>({unread:sm.union(e.unread,sm.MESSAGE)}))}let nu={name:"join",usage:"<name> [password]",description:"Join a channel",execute:(e,t)=>{let s=t[0];if(!s)throw Error("Missing channel name");t.length>1?e.open(s,null,t[1]):e.open(s)}},nc={name:"kick",usage:"<nick> [comment]",description:"Remove a user from the channel",execute:(e,t)=>{let s=t[0],n=[ni(e),s];t.length>1&&n.push(t.slice(1).join(" ")),na(e).send({command:"KICK",params:n})}},nh={name:"ban",usage:"[nick]",description:"Ban a user from the channel, or display the current ban list",execute:(e,t)=>{if(0!==t.length)return no(e,t,"+b");{let t=ni(e);na(e).send({command:"MODE",params:[t,"+b"]})}}};function np(e,t,s){let n=t[0];if(!n)throw Error("Missing nick");let r=ni(e);na(e).send({command:"MODE",params:[r,s,n]})}var nd=new Map([{name:"away",usage:"[message]",description:"Set away message",execute:(e,t)=>{let s=[];t.length&&s.push(t.join(" ")),na(e).send({command:"AWAY",params:s})}},nh,{name:"buffer",usage:"<name>",description:"Switch to a buffer",execute:(e,t)=>{let s=t[0];for(let t of e.state.buffers.values())if(t.name===s)return void e.switchBuffer(t);throw Error("Unknown buffer")}},{name:"close",description:"Close the current buffer",execute:(e,t)=>{let s=e.state.buffers.get(e.state.activeBuffer);if(!s||s.type===sc)throw Error("Not in a user or channel buffer");e.close(s.id)}},{name:"deop",usage:"<nick>",description:"Remove operator status for a user on this channel",execute:(e,t)=>np(e,t,"-o")},{name:"devoice",usage:"<nick>",description:"Remove voiced status for a user on this channel",execute:(e,t)=>np(e,t,"-v")},{name:"disconnect",description:"Disconnect from the server",execute:(e,t)=>{e.disconnect()}},{name:"help",description:"Show help menu",execute:(e,t)=>{e.openHelp()}},{name:"invite",usage:"<nick>",description:"Invite a user to the channel",execute:(e,t)=>{let s=t[0];if(!s)throw Error("Missing nick");let n=ni(e);na(e).send({command:"INVITE",params:[s,n]})}},{...nu,name:"j"},nu,nc,{name:"kickban",usage:"<target>",description:"Ban a user and removes them from the channel",execute:(e,t)=>{nc.execute(e,t),nh.execute(e,t)}},{name:"lusers",usage:"[<mask> [<target>]]",description:"Request user statistics about the network",execute:(e,t)=>{na(e).send({command:"LUSERS",params:t}),nl(e)}},{name:"me",usage:"<action>",description:"Send an action message to the current buffer",execute:(e,t)=>{let s=t.join(" "),n=function(e){let t=e.state.buffers.get(e.state.activeBuffer);if(!t)throw Error("Not in a buffer");return t.name}(e),r=`\x01ACTION ${s}\x01`;e.privmsg(n,r)}},{name:"mode",usage:"[target] [modes] [mode args...]",description:"Query or change a channel or user mode",execute:(e,t)=>{let s=t[0];(!s||s.startsWith("+")||s.startsWith("-"))&&(t=[ni(e),...t]),na(e).send({command:"MODE",params:t})}},{name:"motd",usage:"[server]",description:"Get the Message Of The Day",execute:(e,t)=>{na(e).send({command:"MOTD",params:t}),nl(e)}},{name:"msg",usage:"<target> <message>",description:"Send a message to a nickname or a channel",execute:(e,t)=>{let s=t[0],n=t.slice(1).join(" ");na(e).send({command:"PRIVMSG",params:[s,n]})}},{name:"nick",usage:"<nick>",description:"Change current nickname",execute:(e,t)=>{let s=t[0];na(e).send({command:"NICK",params:[s]})}},{name:"notice",usage:"<target> <message>",description:"Send a notice to a nickname or a channel",execute:(e,t)=>{let s=t[0],n=t.slice(1).join(" ");na(e).send({command:"NOTICE",params:[s,n]})}},{name:"op",usage:"<nick>",description:"Give a user operator status on this channel",execute:(e,t)=>np(e,t,"+o")},{name:"part",usage:"[reason]",description:"Leave a channel",execute:(e,t)=>{let s=t.join(" "),n=[ni(e)];s&&n.push(s),na(e).send({command:"PART",params:n})}},{name:"query",usage:"<nick> [message]",description:"Open a buffer to send messages to a nickname",execute:(e,t)=>{let s=t[0];if(!s)throw Error("Missing nickname");if(e.open(s),t.length>1){let n=t.slice(1).join(" ");e.privmsg(s,n)}}},{name:"quiet",usage:"[nick]",description:"Quiet a user in the channel, or display the current quiet list",execute:(e,t)=>{if(0!==t.length)return no(e,t,"+q");na(e).send({command:"MODE",params:[ni(e),"+q"]})}},{name:"quit",description:"Quit",execute:(e,t)=>{e.close({name:"*"})}},{name:"quote",usage:"<command>",description:"Send a raw IRC command to the server",execute:(e,t)=>{let s;try{s=tL(t.join(" "))}catch(e){throw Error("Failed to parse IRC command",{cause:e})}na(e).send(s)}},{name:"reconnect",description:"Reconnect to the server",execute:(e,t)=>{e.reconnect()}},{name:"setname",usage:"<realname>",description:"Change current realname",execute:(e,t)=>{let s=t.join(" "),n=na(e);if(!n.caps.enabled.has("setname"))throw Error("Server doesn't support changing the realname");n.send({command:"SETNAME",params:[s]})}},{name:"stats",usage:"<query> [server]",description:"Request server statistics",execute:(e,t)=>{let s=t[0];if(!s)throw Error("Missing query");let n=[s];t.length>1&&n.push(t.slice(1).join(" ")),na(e).send({command:"STATS",params:n}),nl(e)}},{name:"topic",usage:"<topic>",description:"Change the topic of the current channel",execute:(e,t)=>{let s=[ni(e)];t.length>0&&s.push(t.join(" ")),na(e).send({command:"TOPIC",params:s})}},{name:"unban",usage:"<nick>",description:"Remove a user from the ban list",execute:(e,t)=>no(e,t,"-b")},{name:"unquiet",usage:"<nick>",description:"Remove a user from the quiet list",execute:(e,t)=>no(e,t,"-q")},{name:"voice",usage:"<nick>",description:"Give a user voiced status on this channel",execute:(e,t)=>np(e,t,"+v")},{name:"who",usage:"<mask>",description:"Retrieve a list of users",execute:(e,t)=>{na(e).send({command:"WHO",params:t}),nl(e)}},{name:"whois",usage:"<nick>",description:"Retrieve information about a user",execute:(e,t)=>{let s=t[0];if(!s)throw Error("Missing nick");na(e).send({command:"WHOIS",params:[s]}),nl(e)}},{name:"whowas",usage:"<nick> [count]",description:"Retrieve information about an offline user",execute:(e,t)=>{if(t.length<1)throw Error("Missing nick");na(e).send({command:"WHOWAS",params:t}),nl(e)}},{name:"list",usage:"[filter]",description:"Retrieve a list of channels from a network",execute:(e,t)=>{na(e).send({command:"LIST",params:t}),nl(e)}}].map(e=>[e.name,e]));function nm(){let e=nr.map(e=>{let t=[];return e.ctrlKey&&t.push("Ctrl"),e.altKey&&t.push("Alt"),t.push(e.key),t=t.map((e,t)=>tI`
				${t>0?"+":null}
				<kbd>${e}</kbd>
			`),tI`
			<dt>${t}</dt>
			<dd>${e.description}</dd>
		`});return e.push(tI`
		<dt><kbd>Tab</kbd></dt>
		<dd>Automatically complete nickname or channel</dd>
	`),window.matchMedia("(pointer: none)").matches||e.push(tI`
			<dt><strong>Middle mouse click</strong></dt>
			<dd>Close buffer</dd>
		`),tI`<dl>${e}</dl>`}function nf(){let e=[...nd.keys()].map(e=>{let t=nd.get(e),s=[tI`<strong>/${e}</strong>`];return t.usage&&s.push(" "+t.usage),tI`
			<dt><code>${s}</code></dt>
			<dd>${t.description}</dd>
		`});return tI`<dl>${e}</dl>`}function ng(){return tI`
		<h3>About</h3>
		<p>
			<strong>gamja</strong> is licensed under <a href="https://www.gnu.org/licenses/agpl-3.0.en.html" target="_blank" rel="noreferrer">AGPLv3</a>.
			Source code is available <a href="https://codeberg.org/emersion/gamja" target="_blank" rel="noreferrer">on Codeberg</a>.
		</p>

		<h3>Key bindings</h3>
		<${nm}/>

		<h3>Commands</h3>
		<${nf}/>
	`}let nb={name:"",host:"",port:6697,nickname:"",username:"",realname:"",pass:""};class nk extends R{prevParams=null;state={...nb,autojoin:!0};constructor(e){super(e),this.prevParams={...nb},this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),e.params&&Object.keys(nb).forEach(t=>{void 0!==e.params[t]&&(this.state[t]=e.params[t],this.prevParams[t]=e.params[t])})}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){e.preventDefault();let t={};Object.keys(nb).forEach(e=>{!this.props.isNew&&this.prevParams[e]===this.state[e]||this.props.isNew&&nb[e]===this.state[e]||(t[e]=this.state[e])});let s=this.state.autojoin?this.props.autojoin:null;this.props.onSubmit(t,s)}render(){let e=null;this.props.isNew||(e=tI`
				<button type="button" class="danger" onClick=${()=>this.props.onRemove()}>
					Remove network
				</button>
			`);let t=null;return this.props.autojoin&&(t=tI`
				<label>
					<input
						type="checkbox"
						name="autojoin"
						checked=${this.state.autojoin}
					/>
					Auto-join channel <strong>${this.props.autojoin}</strong>
				</label>
				<br/><br/>
			`),tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<label>
					Hostname:<br/>
					<input type="text" name="host" value=${this.state.host} autofocus required/>
				</label>
				<br/><br/>

				${t}

				<details>
					<summary role="button">Advanced options</summary>

					<br/>

					<label>
						Port:<br/>
						<input type="number" name="port" value=${this.state.port}/>
					</label>
					<br/><br/>

					<label>
						Network name:<br/>
						<input type="text" name="name" value=${this.state.name}/>
					</label>
					<br/><br/>

					<label>
						Nickname:<br/>
						<input type="username" name="nickname" value=${this.state.nickname}/>
					</label>
					<br/><br/>

					<label>
						Username:<br/>
						<input type="username" name="username" value=${this.state.username}/>
					</label>
					<br/><br/>

					<label>
						Real name:<br/>
						<input type="text" name="realname" value=${this.state.realname}/>
					</label>
					<br/><br/>

					<label>
						Server password:<br/>
						<input type="password" name="pass" value=${this.state.pass} placeholder="None"/>
					</label>
					<br/>
				</details>

				<br/>
				${e}
				${" "}
				<button>
					${this.props.isNew?"Add network":"Save network"}
				</button>
			</form>
		`}}class nv extends R{state={username:"",password:""};constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),e.username&&(this.state.username=e.username)}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){e.preventDefault(),this.props.onSubmit(this.state.username,this.state.password)}render(){return tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<label>
					Username:<br/>
					<input type="username" name="username" value=${this.state.username} required/>
				</label>
				<br/><br/>

				<label>
					Password:<br/>
					<input type="password" name="password" value=${this.state.password} required autofocus/>
				</label>
				<br/><br/>

				<button>Login</button>
			</form>
		`}}class nw extends R{state={email:"",password:""};constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this)}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){e.preventDefault(),this.props.onSubmit(this.state.email,this.state.password)}render(){return tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<label>
					E-mail:<br/>
					<input
						type="email"
						name="email"
						value=${this.state.email}
						required=${this.props.emailRequired}
						placeholder=${this.props.emailRequired?null:"(optional)"}
						autofocus
					/>
				</label>
				<br/><br/>

				<label>
					Password:<br/>
					<input type="password" name="password" value=${this.state.password} required/>
				</label>
				<br/><br/>

				<button>Register</button>
			</form>
		`}}class ny extends R{state={code:""};constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this)}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s})}handleSubmit(e){e.preventDefault(),this.props.onSubmit(this.state.code)}render(){return tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<p>Your account <strong>${this.props.account}</strong> has been created, but a verification code is required to complete the registration.</p>

				<p>${si(this.props.message)}</p>

				<label>
					Verification code:<br/>
					<input type="text" name="code" value=${this.state.code} required autofocus autocomplete="off"/>
				</label>
				<br/><br/>

				<button>Verify account</button>
			</form>
		`}}class nC extends R{state={};constructor(e){super(e),this.state.secondsInTimestamps=e.settings.secondsInTimestamps,this.state.bufferEvents=e.settings.bufferEvents,this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this)}handleInput(e){let t=e.target,s="checkbox"===t.type?t.checked:t.value;this.setState({[t.name]:s},()=>{this.props.onChange(this.state)})}handleSubmit(e){e.preventDefault(),this.props.onClose()}registerProtocol(){let e=window.location.origin+window.location.pathname+"?open=%s";try{navigator.registerProtocolHandler("irc",e),navigator.registerProtocolHandler("ircs",e)}catch(e){console.error("Failed to register protocol handler: ",e)}}render(){let e=null;return this.props.showProtocolHandler&&(e=tI`
				<div class="protocol-handler">
					<div class="left">
						Set gamja as your default IRC client for this browser.
						IRC links will be automatically opened here.
					</div>
					<div class="right">
						<button type="button" onClick=${()=>this.registerProtocol()}>
							Enable
						</button>
					</div>
				</div>
				<br/><br/>
			`),tI`
			<form onInput=${this.handleInput} onSubmit=${this.handleSubmit}>
				<label>
					<input
						type="checkbox"
						name="secondsInTimestamps"
						checked=${this.state.secondsInTimestamps}
					/>
					Show seconds in time indicator
				</label>
				<br/><br/>

				<label>
					<input
						type="radio"
						name="bufferEvents"
						value="fold"
						checked=${"fold"===this.state.bufferEvents}
					/>
					Show and fold chat events
				</label>
				<br/>
				<label>
					<input
						type="radio"
						name="bufferEvents"
						value="expand"
						checked=${"expand"===this.state.bufferEvents}
					/>
					Show and expand chat events
				</label>
				<br/>
				<label>
					<input
						type="radio"
						name="bufferEvents"
						value="hide"
						checked=${"hide"===this.state.bufferEvents}
					/>
					Hide chat events
				</label>
				<br/><br/>

				${e}

				<button type="button" class="danger" onClick=${()=>this.props.onDisconnect()}>
					Disconnect
				</button>
				<button>
					Close
				</button>
			</form>
		`}}class nS extends R{constructor(e){super(e),this.handleClick=this.handleClick.bind(this)}handleClick(e){e.preventDefault(),this.props.onClick()}render(){let e=this.props.selected?"selected":"";return tI`
			<li>
				<a
					href=${sw(this.props.buffer,this.props.bouncerNetwork)}
					class=${e}
					onClick=${this.handleClick}
				>
					<span class="server">
						${sC(this.props.server,this.props.bouncerNetwork)}
					</span>
					${this.props.buffer.name}
				</a>
			</li>
		`}}function nE(e,t){return+!!e.toLowerCase().includes(t)}class n$ extends R{state={query:"",selected:0};constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),this.handleKeyDown=this.handleKeyDown.bind(this)}getSuggestions(){let e=this.state.query.toLowerCase(),t=[],s=new Map;for(let n of this.props.buffers.values()){if(n.type===sc)continue;let r=0;if(""!==e){let t=this.props.servers.get(n.server);if(!(r=function(e,t,s){let n=2*nE(e.name,s);switch(e.type){case sh:n+=nE(e.topic||"",s);break;case sp:let r=t.users.get(e.name);r&&r.realname&&tQ(r.realname,e.name)&&(n+=nE(r.realname,s))}return n}(n,t,e)))continue}s.set(n.id,r),t.push(n)}return t.sort((e,t)=>s.get(t.id)-s.get(e.id)),t.slice(0,20)}handleInput(e){let t=e.target;this.setState({[t.name]:t.value})}handleSubmit(e){e.preventDefault(),this.props.onSubmit(this.getSuggestions()[this.state.selected])}handleKeyDown(e){switch(e.key){case"ArrowUp":e.stopPropagation(),this.move(-1);break;case"ArrowDown":e.stopPropagation(),this.move(1)}}move(e){let t=this.getSuggestions().length;this.setState(s=>({selected:(s.selected+e+t)%t}))}render(){let e=this.getSuggestions().map((e,t)=>{let s=this.props.servers.get(e.server),n=null;return s.bouncerNetID&&(n=this.props.bouncerNetworks.get(s.bouncerNetID)),tI`
				<${nS}
					buffer=${e}
					server=${s}
					bouncerNetwork=${n}
					selected=${this.state.selected===t}
					onClick=${()=>this.props.onSubmit(e)}
				/>
			`});return tI`
			<form
				onInput=${this.handleInput}
				onSubmit=${this.handleSubmit}
				onKeyDown=${this.handleKeyDown}
			>
				<input
					type="search"
					name="query"
					value=${this.state.query}
					placeholder="Filter"
					autocomplete="off"
					autofocus
				/>
				<ul class="switcher-list">
					${e}
				</ul>
			</form>
		`}}let n_=tI`
	<svg width="1em" height="1em" viewBox="0 0 24 24"
		fill="none" xmlns="http://www.w3.org/2000/svg">
		<path d="M12 5L12 19M5 12L19 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
	</svg>
`,nI=tI`
	<svg class="spinner-icon" width="1em" height="1em"
		viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
		<path d="M12 6a6 6 0 0 1 0 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
	</svg>
`,nN=tI`
	<svg class="x-icon" width="1em" height="1em" viewBox="0 0 24 24"
		fill="none" xmlns="http://www.w3.org/2000/svg">
		<path d="M7 7L17 17M17 7L7 17" stroke="currentColor" stroke-width="1.5"
			stroke-linecap="round" stroke-linejoin="round"/>
	</svg>
`;class nx extends R{state={text:"",uploading:!1,dragging:!1};textInput=x();fileInput=x();lastAutocomplete=null;uploadCount=0;uploadAbortController=null;constructor(e){super(e),this.handleInput=this.handleInput.bind(this),this.handleSubmit=this.handleSubmit.bind(this),this.handleInputKeyDown=this.handleInputKeyDown.bind(this),this.handleInputPaste=this.handleInputPaste.bind(this),this.handleDragEnter=this.handleDragEnter.bind(this),this.handleDragLeave=this.handleDragLeave.bind(this),this.handleDragOver=this.handleDragOver.bind(this),this.handleDrop=this.handleDrop.bind(this),this.handleWindowKeyDown=this.handleWindowKeyDown.bind(this),this.handleWindowPaste=this.handleWindowPaste.bind(this),this.handleUploadClick=this.handleUploadClick.bind(this),this.handleCancelClick=this.handleCancelClick.bind(this),this.handleFileInputChange=this.handleFileInputChange.bind(this)}handleInput(e){this.setState({[e.target.name]:e.target.value}),this.props.readOnly&&"text"===e.target.name&&!e.target.value&&e.target.blur()}handleSubmit(e){e.preventDefault(),this.props.onSubmit(this.state.text),this.setState({text:""})}handleInputKeyDown(e){let t,s=e.target;if(!this.props.autocomplete||"Tab"!==e.key||s.selectionStart!==s.selectionEnd)return;e.preventDefault();let n=s.selectionStart,r=this.state.text;if(this.lastAutocomplete&&this.lastAutocomplete.text===r&&this.lastAutocomplete.carretPos===n)t=this.lastAutocomplete;else{let e,a;for(this.lastAutocomplete=null,e=n-1;e>=0&&" "!==r[e];e--);for(e++,a=n;a<r.length&&" "!==r[a];a++);let i=r.slice(e,a);if(!i)return;let o=this.props.autocomplete(i);if(0===o.length)return;t={text:r,carretPos:s.selectionStart,prefix:r.slice(0,e),suffix:r.slice(a),replacements:o,replIndex:-1}}let a=t.replacements.length;e.shiftKey?t.replIndex--:t.replIndex++,t.replIndex=(t.replIndex+a)%a;let i=t.replacements[t.replIndex];t.prefix||t.suffix||(i.startsWith("/")?i+=" ":i+=": "),t.text=t.prefix+i+t.suffix,t.carretPos=t.prefix.length+i.length,s.value=t.text,s.selectionStart=t.carretPos,s.selectionEnd=s.selectionStart,this.lastAutocomplete=t,this.setState({text:t.text})}canUploadFiles(){let e=this.props.client;return e&&e.isupport.filehost()&&!this.props.readOnly}async uploadFile(e,t){var s;let n,r,a=this.props.client,i=a.isupport.filehost();if(a.params.saslPlain){let e=a.params.saslPlain;r="Basic "+btoa(e.username+":"+e.password)}else a.params.saslOauthBearer&&(r="Bearer "+a.params.saslOauthBearer.token);let o={"Content-Length":e.size,"Content-Disposition":(n=encodeURIComponent(s=e.name))===s?'attachment; filename="'+s+'"':"attachment; filename*=UTF-8''"+n};e.type&&(o["Content-Type"]=e.type),r&&(o.Authorization=r);let l=await fetch(i,{method:"POST",body:e,headers:o,credentials:"include",signal:t});if(!l.ok)throw Error(`HTTP request failed (${l.status})`);let u=l.headers.get("Location");if(!u)throw Error("filehost response missing Location header field");return new URL(u,i)}async uploadFileList(e){let t;this.uploadAbortController||(this.uploadAbortController=new AbortController);let s=this.uploadAbortController.signal;this.uploadCount++,this.setState({uploading:!0});let n=[];for(let t of e)n.push(this.uploadFile(t,s));try{t=await Promise.all(n)}catch(e){s.aborted||this.props.onError(Error("Failed to upload files",{cause:e}));return}finally{this.uploadCount--,0===this.uploadCount&&(this.uploadAbortController=null,this.setState({uploading:!1}))}this.setState(e=>e.text?{text:e.text+" "+t.join(" ")}:{text:t.join(" ")})}async handleInputPaste(e){0!==e.clipboardData.files.length&&this.canUploadFiles()&&(e.preventDefault(),e.stopImmediatePropagation(),await this.uploadFileList(e.clipboardData.files))}isDraggingFiles(e){return Array.from(e.dataTransfer.items).every(e=>"file"===e.kind)}handleDragEnter(e){this.canUploadFiles()&&this.isDraggingFiles(e)&&this.setState({dragging:!0})}handleDragLeave(e){this.base.contains(e.relatedTarget)||this.setState({dragging:!1})}handleDragOver(e){this.canUploadFiles()&&this.isDraggingFiles(e)&&e.preventDefault()}async handleDrop(e){0!==e.dataTransfer.files.length&&this.canUploadFiles()&&(e.preventDefault(),e.stopImmediatePropagation(),this.setState({dragging:!1}),this.textInput.current.focus(),await this.uploadFileList(e.dataTransfer.files))}handleUploadClick(e){e.preventDefault(),this.textInput.current.focus(),this.fileInput.current.click()}handleCancelClick(e){e.preventDefault(),this.uploadAbortController.abort()}async handleFileInputChange(e){let t=e.target.files;0!==t.length&&(await this.uploadFileList(t),e.target.value="")}handleWindowKeyDown(e){if(document.activeElement&&document.activeElement!==document.body)switch(document.activeElement.tagName.toLowerCase()){case"section":case"a":break;default:return}e.altKey||e.ctrlKey||e.metaKey||1!=[...e.key].length||this.state.text||this.props.readOnly||this.props.commandOnly&&"/"!==e.key||(e.preventDefault(),this.setState({text:e.key},()=>{this.focus()}))}handleWindowPaste(e){if(document.activeElement!==document.body&&"SECTION"!==document.activeElement.tagName||this.props.readOnly||!this.textInput.current)return;if(e.clipboardData.files.length>0)return void this.handleInputPaste(e);let t=e.clipboardData.getData("text");e.preventDefault(),e.stopImmediatePropagation(),this.textInput.current.focus(),this.textInput.current.setRangeText(t,void 0,void 0,"end"),this.setState({text:this.textInput.current.value})}componentDidMount(){window.addEventListener("keydown",this.handleWindowKeyDown),window.addEventListener("paste",this.handleWindowPaste)}componentWillUnmount(){window.removeEventListener("keydown",this.handleWindowKeyDown),window.removeEventListener("paste",this.handleWindowPaste)}focus(){this.textInput.current&&(document.activeElement.blur(),this.textInput.current.focus())}render(){let e=[];this.props.readOnly&&!this.state.text&&e.push("read-only"),this.state.uploading&&e.push("uploading"),this.state.dragging&&e.push("dragging");let t=e.join(" "),s="Type a message";this.props.commandOnly&&(s="Type a command (see /help)");let n=null;return this.canUploadFiles()&&(n=tI`
				<div id="composer-buttons">
					${this.state.uploading&&tI`
						<button
							type="button"
							id="composer-spinner"
							title="Cancel upload"
							onClick=${this.handleCancelClick}
						>
							${nI}${nN}
						</button>
					`}
					<button
						type="button"
						id="composer-upload"
						title="Upload file"
						onClick=${this.handleUploadClick}
					>
						${n_}
					</button>
				</div>
				<input
					type="file"
					ref=${this.fileInput}
					multiple
					style="display: none"
					onChange=${this.handleFileInputChange}
				/>
			`),tI`
			<form
				id="composer"
				class=${t}
				onInput=${this.handleInput}
				onSubmit=${this.handleSubmit}
				onDragEnter=${this.handleDragEnter}
				onDragLeave=${this.handleDragLeave}
				onDragOver=${this.handleDragOver}
				onDrop=${this.handleDrop}
			>
				<input
					type="text"
					name="text"
					ref=${this.textInput}
					value=${this.state.text}
					autocomplete="off"
					placeholder=${s}
					enterkeyhint="send"
					onKeyDown=${this.handleInputKeyDown}
					onPaste=${this.handleInputPaste}
					maxlength=${this.props.maxLen}
				/>
				${n}
			</form>
		`}}let nT=new Map;class nR extends R{constructor(e){super(e),this.handleScroll=sA(this.handleScroll.bind(this),100),this.handleResize=this.handleResize.bind(this),this.stickToBottom=!0,this.resizeObserver=new ResizeObserver(this.handleResize)}isAtBottom(){let e=this.props.target.current;return 10>=Math.abs(e.scrollHeight-e.clientHeight-e.scrollTop)}saveScrollPosition(e){let t=this.props.target.current,s=t.querySelectorAll(this.props.stickTo),n=null;if(!this.isAtBottom())for(let e=0;e<s.length;e++){let r=s[e];if(r.offsetTop>=t.scrollTop+t.offsetTop){n=r.dataset.key;break}}nT.set(e,n)}restoreScrollPosition(){let e=this.props.target.current;if(!e.firstChild)return;let t=nT.get(this.props.scrollKey);if(t){let s=e.querySelector('[data-key="'+t+'"]');s&&s.scrollIntoView()}else e.firstChild.scrollIntoView({block:"end"});this.stickToBottom=!t,0===e.scrollTop&&this.props.onScrollTop()}handleScroll(){0===this.props.target.current.scrollTop&&this.props.onScrollTop(),this.stickToBottom=this.isAtBottom()}handleResize(){this.stickToBottom&&this.props.target.current.firstChild&&this.props.target.current.firstChild.scrollIntoView({block:"end"})}componentDidMount(){this.restoreScrollPosition(),this.props.target.current.addEventListener("scroll",this.handleScroll),this.resizeObserver.observe(this.props.target.current)}getSnapshotBeforeUpdate(e){(this.props.scrollKey!==e.scrollKey||this.props.children!==e.children)&&this.saveScrollPosition(e.scrollKey)}componentDidUpdate(e){this.props.target.current&&this.restoreScrollPosition()}componentWillUnmount(){this.resizeObserver.unobserve(this.props.target.current),this.props.target.current.removeEventListener("scroll",this.handleScroll),this.saveScrollPosition(this.props.scrollKey)}render(){return this.props.children}}class nD extends R{body=x();constructor(e){super(e),this.handleCloseClick=this.handleCloseClick.bind(this),this.handleBackdropClick=this.handleBackdropClick.bind(this),this.handleKeyDown=this.handleKeyDown.bind(this)}dismiss(){this.props.onDismiss()}handleCloseClick(e){e.preventDefault(),this.dismiss()}handleBackdropClick(e){"dialog"===e.target.className&&this.dismiss()}handleKeyDown(e){"Escape"===e.key&&this.dismiss()}componentDidMount(){window.addEventListener("keydown",this.handleKeyDown);let e=this.body.current.querySelector("input[autofocus]");e&&e.focus()}componentWillUnmount(){window.removeEventListener("keydown",this.handleKeyDown)}render(){return tI`
			<div class="dialog" onClick=${this.handleBackdropClick} role="dialog" aria-modal="true">
				<div class="dialog-body" ref=${this.body}>
					<div class="dialog-header">
						<h2>${this.props.title}</h2>
						<button class="dialog-close" onClick=${this.handleCloseClick} title="Close">×</button>
					</div>
					${this.props.children}
				</div>
			</div>
		`}}let nO={server:{}},nA=fetch("./config.json").then(e=>e.ok?e.json():(404!==e.status&&console.error("Failed to fetch config: HTTP error:",e.status,e.statusText),{})).catch(e=>(console.error("Failed to fetch config:",e),{})).then(e=>({...nO,...e}));function nL(e,t){return e&&e.receipts?e.receipts[sg]:null}function nM(e){let t;if(e.startsWith("#/")&&(e=e.substring(1)),e.startsWith("//")){let s=(e=e.substring(2)).indexOf("/");s<0&&(s=e.length),t=e.substring(0,s),e=e.substring(s)}return e.startsWith("/")&&(e=e.substring(1)),{host:t,entity:e}}let nP=0;class nB extends R{state={...sO.create(),connectParams:{url:null,pass:null,username:null,realname:null,nick:null,saslPlain:null,saslExternal:!1,autoconnect:!1,autojoin:[],ping:0},connectForm:!0,loading:!0,dialog:null,dialogData:null,error:null,openPanels:{bufferList:!1,memberList:!1}};debug=!function(){try{return!0}catch(e){return!1}}();config={...nO};clients=new Map;endOfHistory=new Map;receipts=new Map;buffer=x();composer=x();switchToChannel=null;autoOpenURL=null;initialRoute=null;messageNotifications=new Set;baseTitle=null;lastFocusPingDate=null;constructor(e){super(e),this.handleConnectSubmit=this.handleConnectSubmit.bind(this),this.handleJoinSubmit=this.handleJoinSubmit.bind(this),this.handleConfirmOpenBufferSubmit=this.handleConfirmOpenBufferSubmit.bind(this),this.handleBufferListClick=this.handleBufferListClick.bind(this),this.handleBufferListClose=this.handleBufferListClose.bind(this),this.toggleBufferList=this.toggleBufferList.bind(this),this.toggleMemberList=this.toggleMemberList.bind(this),this.handleComposerSubmit=this.handleComposerSubmit.bind(this),this.handleChannelClick=this.handleChannelClick.bind(this),this.handleNickClick=this.handleNickClick.bind(this),this.autocomplete=this.autocomplete.bind(this),this.handleBufferScrollTop=this.handleBufferScrollTop.bind(this),this.dismissDialog=this.dismissDialog.bind(this),this.handleAddNetworkClick=this.handleAddNetworkClick.bind(this),this.handleNetworkSubmit=this.handleNetworkSubmit.bind(this),this.handleNetworkRemove=this.handleNetworkRemove.bind(this),this.showError=this.showError.bind(this),this.handleDismissError=this.handleDismissError.bind(this),this.handleAuthSubmit=this.handleAuthSubmit.bind(this),this.handleRegisterSubmit=this.handleRegisterSubmit.bind(this),this.handleVerifyClick=this.handleVerifyClick.bind(this),this.handleVerifySubmit=this.handleVerifySubmit.bind(this),this.handleOpenSettingsClick=this.handleOpenSettingsClick.bind(this),this.handleSettingsChange=this.handleSettingsChange.bind(this),this.handleSettingsDisconnect=this.handleSettingsDisconnect.bind(this),this.handleSwitchSubmit=this.handleSwitchSubmit.bind(this),this.handleWindowFocus=this.handleWindowFocus.bind(this),this.handleWindowHashChange=this.handleWindowHashChange.bind(this),this.state.settings={...this.state.settings,...sB.load()},this.bufferStore=new sH,nA.then(e=>(this.handleConfig(e),e))}async handleConfig(e){let t,s,n={...this.state.connectParams};"string"==typeof e.server.url&&(n.url=e.server.url),Array.isArray(e.server.autojoin)?n.autojoin=e.server.autojoin:"string"==typeof e.server.autojoin&&(n.autojoin=[e.server.autojoin]),"string"==typeof e.server.nick&&(n.nick=e.server.nick),"boolean"==typeof e.server.autoconnect&&(n.autoconnect=e.server.autoconnect),"external"===e.server.auth&&(n.saslExternal=!0),"number"==typeof e.server.ping&&(n.ping=e.server.ping),n.autoconnect&&"mandatory"===e.server.auth&&(console.error('Error in config.json: cannot set server.autoconnect = true and server.auth = "mandatory"'),n.autoconnect=!1),"oauth2"!==e.server.auth||e.oauth2&&e.oauth2.url&&e.oauth2.client_id||(console.error('Error in config.json: server.auth = "oauth2" requires oauth2 settings'),e.server.auth=null);let r=sM.load();r&&(n={...n,...r,autoconnect:!0,autojoin:[]});let a=[],i=(t=window.location.search.substring(1),s={},t.split("&").forEach(e=>{if(!e)return;let t=e.split("=");s[decodeURIComponent(t[0])]=decodeURIComponent(t[1]||"")}),s);if("string"!=typeof i.server||n.url&&i.server||(n.url=i.server,e.server.auth=null),"string"==typeof i.nick&&(n.nick=i.nick),"string"==typeof i.channels&&(a=i.channels.split(",")),"string"==typeof i.open&&(this.autoOpenURL=tJ(i.open)),"1"===i.debug?this.debug=!0:"0"===i.debug&&(this.debug=!1),window.location.hash&&(window.location.hash.startsWith("#/")?this.initialRoute=nM(window.location.hash):a=window.location.hash.split(",")),this.config=e,!n.nick&&n.autoconnect&&(n.nick="user-*"),n.nick&&n.nick.includes("*")){let e=Math.random().toString(36).substr(2,7);n.nick=n.nick.replace("*",e)}if("oauth2"===e.server.auth&&!n.saslOauthBearer){let e;if(i.error){console.error("OAuth 2.0 authorization failed: ",i.error),this.showError("Authentication failed: "+(i.error_description||i.error));return}if(!i.code)return void this.redirectOauth2Authorize();let t=new URL(window.location.toString());t.searchParams.delete("code"),t.searchParams.delete("state"),window.history.replaceState(null,"",t.toString());try{e=await this.exchangeOauth2Code(i.code)}catch(e){this.showError(e);return}n.saslOauthBearer=e,e.username&&!n.nick&&(n.nick=e.username)}a.length>0&&(n.autoconnect?this.autoOpenURL={host:"",entity:a[0]}:n.autojoin=a),this.setState({loading:!1,connectParams:n}),n.autoconnect&&(this.setState({connectForm:!1}),this.connect(n))}async redirectOauth2Authorize(){let e;try{e=await st(this.config.oauth2.url)}catch(e){console.error("Failed to fetch OAuth 2.0 server metadata:",e),this.showError("Failed to fetch OAuth 2.0 server metadata");return}!function({serverMetadata:e,clientId:t,redirectUri:s,scope:n}){let r={response_type:"code",client_id:t,redirect_uri:s};n&&(r.scope=n),window.location.assign(e.authorization_endpoint+"?"+se(r))}({serverMetadata:e,clientId:this.config.oauth2.client_id,redirectUri:window.location.toString(),scope:this.config.oauth2.scope})}async exchangeOauth2Code(e){let t=await st(this.config.oauth2.url),s=new URL(window.location.toString());s.searchParams.delete("code"),s.searchParams.delete("state");let n=(await sn({serverMetadata:t,redirectUri:s.toString(),code:e,clientId:this.config.oauth2.client_id,clientSecret:this.config.oauth2.client_secret})).access_token,r=null;if(t.introspection_endpoint)try{(r=(await sr({serverMetadata:t,token:n,clientId:this.config.oauth2.client_id,clientSecret:this.config.oauth2.client_secret})).username)||console.warn("Username missing from OAuth 2.0 token introspection response")}catch(e){console.warn("Failed to introspect OAuth 2.0 token:",e)}return{token:n,username:r}}showError(e){let t;if(console.error("App error: ",e),e instanceof Error){let s=[];for(;e;)s.push(e.message),e=e.cause;t=s.join(": ")}else t=String(e);return this.setState({error:t}),++nP}dismissError(e){e&&e!==nP||this.setState({error:null})}handleDismissError(e){e.preventDefault(),this.dismissError()}setServerState(e,t,s){this.setState(s=>sO.updateServer(s,e,t),s)}setBufferState(e,t,s){this.setState(s=>sO.updateBuffer(s,e,t),s)}syncBufferUnread(e,t){let s=this.clients.get(e),n=this.bufferStore.get({name:t,server:s.params});s.caps.enabled.has("draft/chathistory")&&n&&this.setBufferState({server:e,name:t},{unread:n.unread},()=>{this.updateDocumentTitle()}),this.bufferStore.put({name:t,server:s.params,closed:!1})}createBuffer(e,t){let s=this.clients.get(e),n=null,r=!1;return this.setState(a=>{let i;return[n,i]=sO.createBuffer(a,t,e,s),r=!!i,i}),r&&this.syncBufferUnread(e,t),n}sendReadReceipt(e,t){if(!e.supportsReadMarker())return;let s=t.receipts[sg];"*"!==t.name&&s&&e.setReadMarker(t.name,s.time)}switchBuffer(e){let t,s;this.setState(n=>{if(!(t=sO.getBuffer(n,e)))return;let r=this.clients.get(t.server),a=nL(this.bufferStore.get({name:t.name,server:r.params}),sg),i=sO.updateBuffer(n,t.id,{prevReadReceipt:a});return s=!n.activeBuffer,{activeBuffer:t.id,...i}},()=>{if(!t)return;this.buffer.current&&this.buffer.current.focus();let e=this.state.servers.get(t.server);t.type!==sp||e.users.has(t.name)||this.whoUserBuffer(t.name,t.server),t.type!==sh||t.hasInitialWho||this.whoChannelBuffer(t.name,t.server),s&&this.initialRoute||this.updateWindowHash(),this.updateDocumentTitle()}),this.markBufferAsRead(e)}markBufferAsRead(e){let t;this.setState(s=>{if(t=sO.getBuffer(s,e))return sO.updateBuffer(s,t.id,{unread:sm.NONE})},()=>{if(!t)return;let e=this.clients.get(t.server);for(let s of this.messageNotifications)e.cm(s.data.bufferName)===e.cm(t.name)&&s.close();if(t.messages.length>0){let s=t.messages[t.messages.length-1],n={name:t.name,server:e.params,unread:sm.NONE,receipts:{[sg]:sS(s)}};this.bufferStore.put(n)&&this.sendReadReceipt(e,n)}this.updateDocumentTitle()})}updateDocumentTitle(){let e,t,s=sO.getBuffer(this.state,this.state.activeBuffer);s&&(e=this.state.servers.get(s.server)),e.bouncerNetID&&(t=this.state.bouncerNetworks.get(e.bouncerNetID));let n=0;for(let e of this.state.buffers.values())sm.compare(e.unread,sm.HIGHLIGHT)>=0&&n++;let r=[];s&&s.type!==sc&&r.push(s.name),t&&r.push(sC(e,t)),r.push(this.baseTitle);let a="";n>0&&(a=`(${n}) `),a+=r.join(" · "),document.title=a}computeWindowHash(){let e,t,s=sO.getBuffer(this.state,this.state.activeBuffer);s&&(e=this.state.servers.get(s.server)),e.bouncerNetID&&(t=this.state.bouncerNetworks.get(e.bouncerNetID));let n="#";return t&&(n+="//"+t.host),s&&(!t&&s.name.startsWith("/")&&(n+="//"),s.type===sc?"#"===n&&(n+="/"):n+="/"+s.name),n}updateWindowHash(){window.location=this.computeWindowHash()}prepareChatMessage(e,t){if(void 0===t.isHighlight){let s=this.clients.get(e);t.isHighlight=function(e,t,s){if("PRIVMSG"!==e.command&&"NOTICE"!==e.command||(t=s(t),e.prefix&&s(e.prefix.name)===t))return!1;let n=s(e.params[1]);for(;;){let e=n.indexOf(t);if(e<0)return!1;let s="\0",r="\0";if(e>0&&(s=n[e-1]),e+t.length<n.length&&(r=n[e+t.length]),tj(s)&&tj(r)&&!function(e){for(let t=e.length-1;t>=0;t--)if(tH.test(e[t])){e=e.slice(t);break}let t=e.indexOf("://");if(t<=0)return!1;let s=e[t-1];switch(s){case"+":case"-":case".":return!0;default:return tB.test(s)}}(n.slice(0,e)))return!0;n=n.slice(e+t.length)}}(t,s.nick,s.cm)||("PRIVMSG"===t.command||"NOTICE"===t.command)&&t.params[0].startsWith("$")}t.tags||(t.tags={}),t.tags.time||(t.tags.time=tW(new Date))}handleChatMessage(e,t,s){let n=this.clients.get(e);this.prepareChatMessage(e,s);let r=this.bufferStore.get({name:t,server:n.params}),a=nL(r,sf),i=nL(r,sg),o=s$(s,a),l=s$(s,i);n.isMyNick(s.prefix.name)&&(l=!0);let u=sm.NONE;if(("PRIVMSG"===s.command||"NOTICE"===s.command)&&!l){let r,a=s.params[0],i=s.params[1];if(s.isHighlight?(u=sm.HIGHLIGHT,r="highlight"):n.isMyNick(a)?(u=sm.HIGHLIGHT,r="private message"):u=sm.MESSAGE,u===sm.HIGHLIGHT&&!o&&!tF(s)){let a="New "+r+" from "+s.prefix.name;n.isChannel(t)&&(a+=" in "+t);let o=function(e,t){if(!window.Notification||"granted"!==Notification.permission)return null;try{return new Notification(e,t)}catch(e){return console.error("Failed to show notification: ",e),null}}(a,{body:su(i),requireInteraction:!0,tag:"msg,server="+e+",from="+s.prefix.name+",to="+t,data:{bufferName:t,message:s}});o&&(o.addEventListener("click",()=>{this.switchBuffer({server:e,name:t})}),o.addEventListener("close",()=>{this.messageNotifications.delete(o)}),this.messageNotifications.add(o))}}if("INVITE"===s.command&&n.isMyNick(s.params[0])){u=sm.HIGHLIGHT;let r=s.params[1],a=new Notification("Invitation to "+r,{body:s.prefix.name+" has invited you to "+r,requireInteraction:!0,tag:"invite,server="+e+",from="+s.prefix.name+",channel="+r,actions:[{action:"accept",title:"Accept"}]});a&&a.addEventListener("click",a=>{if("accept"===a.action){let a={name:t,server:n.params,receipts:{[sg]:sS(s)}};this.bufferStore.put(a)&&this.sendReadReceipt(n,a),this.open(r,e)}else this.switchBuffer({server:e,name:t})})}"PART"!==s.command&&"QUIT"!==s.command&&"730"!==s.command&&"731"!==s.command&&this.createBuffer(e,t);let c={server:e,name:t};this.setState(e=>sO.addMessage(e,s,c)),this.setBufferState(c,e=>{let t=e.unread,r=e.prevReadReceipt,a={[sf]:sS(s)};this.state.activeBuffer===e.id&&document.hasFocus()?a[sg]=sS(s):t=sm.union(t,u),n.isMyNick(s.prefix.name)&&!s$(s,r)&&(r=sS(s));let i={name:e.name,server:n.params,unread:t,receipts:a};return this.bufferStore.put(i)&&this.sendReadReceipt(n,i),{unread:t,prevReadReceipt:r}},()=>{u===sm.HIGHLIGHT&&this.updateDocumentTitle()})}connect(e){var t;let s,n,r;e={...this.state.connectParams,...e};let a=null;this.setState(e=>{let t;return[a,t]=sO.createServer(e),t}),this.setState({connectParams:e});let i=new t8({...(t=e,s=window.location.host||"localhost:8080",n="wss:","https:"!==window.location.protocol&&(n="ws:"),r=window.location.pathname||"/",!window.location.host&&(r="/"),!(t={...t}).url&&(t.url=n+"//"+s+r+"socket"),t.url.startsWith("/")&&(t.url=n+"//"+s+t.url),0>t.url.indexOf("://")&&(t.url=n+"//"+t.url),!t.username&&(t.username=t.nick),!t.realname&&(t.realname=t.nick),t),eventPlayback:this.state.settings.bufferEvents!==sk});i.debug=this.debug,this.clients.set(a,i),this.setServerState(a,{status:i.status});let o=null;i.addEventListener("status",()=>{switch(this.setServerState(a,{status:i.status}),i.status){case t8.Status.DISCONNECTED:this.setServerState(a,{account:null}),this.setState(e=>{let t=new Map(e.buffers);return e.buffers.forEach(e=>{e.server===a&&t.set(e.id,{...e,joined:!1})}),{buffers:t}});break;case t8.Status.REGISTERED:this.setState({connectForm:!1}),o&&this.dismissError(o)}}),i.addEventListener("message",e=>{this.handleMessage(a,e.detail.message)}),i.addEventListener("error",e=>{o=this.showError(e.detail)}),this.createBuffer(a,"*"),this.state.activeBuffer||this.switchBuffer({server:a,name:"*"}),e.autojoin.length>0&&(this.switchToChannel=e.autojoin[0])}disconnect(e){e||(e=sO.getActiveServerID(this.state));let t=this.clients.get(e);t&&(this.clients.delete(e),t.disconnect())}reconnect(e){e||(e=sO.getActiveServerID(this.state));let t=this.clients.get(e);t&&t.reconnect()}serverFromBouncerNetwork(e){for(let[t,s]of this.clients)if(s.params.bouncerNetwork===e)return t;return null}routeMessage(e,t){let s,n,r,a=this.clients.get(e),i=tV(t,"chathistory");if(t.internal)return[];switch(t.command){case"MODE":if(s=t.params[0],a.isChannel(s))return[s];return["*"];case"NOTICE":case"PRIVMSG":if(s=t.params[0],a.isMyNick(s))if(a.cm(t.prefix.name)===a.cm(a.serverPrefix.name))s="*";else{let n=t.tags["+draft/channel-context"];s=n&&a.isChannel(n)&&sO.getBuffer(this.state,{server:e,name:n})?n:t.prefix.name}let o=a.isupport.statusMsg();if(o){let e=tP(s,o);a.isChannel(e.name)&&(s=e.name)}let l=!0;if("PRIVMSG"!==t.command)l=!1;else{let e=tF(t);e&&"ACTION"!==e.command&&(l=!1)}return l||sO.getBuffer(this.state,{server:e,name:s})||(s="*"),[s];case"JOIN":if(n=t.params[0],!a.isMyNick(t.prefix.name))return[n];return[];case"PART":case"KICK":case"TOPIC":return[n=t.params[0]];case"QUIT":return r=[],i?r.push(i.params[0]):this.state.buffers.forEach(s=>{s.server!==e||s.members.has(t.prefix.name)&&r.push(s.name)}),r;case"NICK":let u=t.params[0];return r=[],i?r.push(i.params[0]):(this.state.buffers.forEach(s=>{s.server!==e||s.members.has(t.prefix.name)&&r.push(s.name)}),a.isMyNick(u)&&r.push("*")),r;case"INVITE":let c=n=t.params[1];return sO.getBuffer(this.state,{server:e,name:n})||(c="*"),[c];case"324":case"329":case"346":case"347":case"348":case"349":case"367":case"368":case"728":case"729":return[n=t.params[1]];case"341":return[n=t.params[2]];case"730":case"731":let h=t.params[1].split(",");for(let e of(r=[],h)){let t=tA(e);r.push(t.name)}return r;case"002":case"004":case"005":case"376":case"422":case"301":case"331":case"332":case"333":case"353":case"366":case"903":case"328":case"AWAY":case"SETNAME":case"CHGHOST":case"ACCOUNT":case"CAP":case"AUTHENTICATE":case"PING":case"PONG":case"BATCH":case"TAGMSG":case"CHATHISTORY":case"ACK":case"BOUNCER":case"MARKREAD":case"REDACT":return[];default:return["*"]}}handleMessage(e,t){let s,n,r,a=this.clients.get(e);if(tV(t,"chathistory"))return;let i=this.routeMessage(e,t);switch(this.setState(s=>sO.handleMessage(s,t,e,a)),t.command){case"001":this.fetchBacklog(e);break;case"376":case"422":let o=[];for(let t of this.bufferStore.list(a.params))if("*"!==t.name&&!t.closed)if(a.isChannel(t.name)){if(a.caps.enabled.has("soju.im/bouncer-networks"))continue;o.push(t.name)}else this.createBuffer(e,t.name),this.whoUserBuffer(t.name,e);let l=this.state.servers.get(e).bouncerNetID,u=null;l&&(u=this.state.bouncerNetworks.get(l)),u&&"connected"!==u.state||(o=o.concat(a.params.autojoin),a.params.autojoin=[]),o.length>0&&a.send({command:"JOIN",params:[o.join(",")]});let c=u?u.host:"";if(this.autoOpenURL&&c===this.autoOpenURL.host){let e=this.autoOpenURL;this.autoOpenURL=null,a.ping().then(()=>this.openURL(e))}else this.initialRoute&&c===(this.initialRoute.host||"")&&(this.initialRoute=null,a.ping().then(()=>this.handleWindowHashChange()));break;case"JOIN":n=t.params[0],a.isMyNick(t.prefix.name)&&this.syncBufferUnread(e,n),n===this.switchToChannel&&(this.switchBuffer({server:e,name:n}),this.switchToChannel=null);break;case"BOUNCER":if("NETWORK"!==t.params[0]||a.isupport.bouncerNetID())break;let h=t.params[1],p=null;"*"!==t.params[2]&&(p=tD(t.params[2]));let d=!1;this.setState(e=>p?(d=!e.bouncerNetworks.has(h),sO.storeBouncerNetwork(e,h,p)):sO.deleteBouncerNetwork(e,h),()=>{if(p)d&&this.connect({...a.params,bouncerNetwork:h});else{let e=this.serverFromBouncerNetwork(h);e&&this.close({server:e,name:"*"})}if(p&&"connected"===p.state){let e=this.serverFromBouncerNetwork(h),t=this.clients.get(e);t&&t.status===t8.Status.REGISTERED&&t.params.autojoin&&t.params.autojoin.length>0&&(t.send({command:"JOIN",params:[t.params.autojoin.join(",")]}),t.params.autojoin=[])}});break;case"BATCH":if(!t.params[0].startsWith("-"))break;let m=t.params[0].slice(1),f=a.batches.get(m);if(!f||"soju.im/bouncer-networks"!==f.type)break;this.autoOpenURL&&this.autoOpenURL.host&&!this.findBouncerNetIDByHost(this.autoOpenURL.host)&&(this.openURL(this.autoOpenURL),this.autoOpenURL=null);break;case"MARKREAD":s=t.params[0];let g=t.params[1];if("*"===g||!g.startsWith("timestamp="))break;let b={time:g.replace("timestamp=","")};if(sE(b,nL(this.bufferStore.get({name:s,server:a.params}),sg)))break;for(let e of this.messageNotifications)a.cm(e.data.bufferName)===a.cm(s)&&s$(e.data.message,b)&&e.close();let k=!0;this.setBufferState({server:e,name:s},e=>{k=!1,r=sm.NONE;for(let t=e.messages.length-1;t>=0;t--){let s=e.messages[t];if("PRIVMSG"===s.command||"NOTICE"===s.command){if(s$(s,b))break;if(s.isHighlight||a.isMyNick(e.name)){r=sm.HIGHLIGHT;break}r=sm.MESSAGE}}return{unread:r}},()=>{this.bufferStore.put({name:s,server:a.params,unread:r,closed:k,receipts:{[sg]:b}}),this.updateDocumentTitle()});break;default:if(tU(t.command)&&"422"!==t.command){let e=t.params[t.params.length-1];this.showError(e)}}i.forEach(s=>{this.handleChatMessage(e,s,t)})}async fetchBacklog(e){let t=this.clients.get(e);if(!t.caps.enabled.has("draft/chathistory")||t.caps.enabled.has("soju.im/bouncer-networks")&&!t.params.bouncerNetwork)return;let s=function(e,t,s){let n=e.list(t),r=null;for(let e of n){if("*"===e.name)continue;let t=nL(e,s);sE(r,t)&&(r=t)}return r}(this.bufferStore,t.params,sf);if(!s)return;let n=tW(new Date);(await t.fetchHistoryTargets(n,s.time)).forEach(async r=>{let a,i=s,o=nL(this.bufferStore.get({name:r.name,server:t.params}),sg);sE(i,o)&&(i=o);let l=sO.getBuffer(this.state,{server:e,name:r.name});l&&l.messages.length>0&&(i=sS(l.messages[l.messages.length-1])),t.supportsReadMarker()&&t.isNick(r.name)&&t.fetchReadMarker(r.name);try{a=await t.fetchHistoryBetween(r.name,i,{time:n},4e3)}catch(e){console.error("Failed to fetch backlog for '"+r.name+"': ",e),this.showError("Failed to fetch backlog for '"+r.name+"'");return}for(let t of a.messages)for(let s of this.routeMessage(e,t))this.handleChatMessage(e,s,t)})}handleConnectSubmit(e){this.dismissError(),e.autoconnect?sM.put(e):sM.put(null);let t=this.state.buffers.get(this.state.activeBuffer);t&&this.close(t.server),this.connect(e)}handleChannelClick(e){this.openURL(e.target.href)&&e.preventDefault()}findBouncerNetIDByHost(e){for(let[t,s]of this.state.bouncerNetworks)if(s.host===e)return t;return null}openURL(e){var t;let s,n,r,a;if("string"==typeof e&&(e=tJ(e)),!e)return!1;let{host:i,port:o}=(n=t=e.host,r=null,(a=t.lastIndexOf(":"))>0&&!t.endsWith("]")&&(n=t.slice(0,a),r=parseInt(t.slice(a+1),10)),n.startsWith("[")&&n.endsWith("]")&&(n=n.slice(1,n.length-1)),{host:n,port:r});if(e.host){let t=this.findBouncerNetIDByHost(i);if(!t){let t=this.clients.values().next().value;if(!t||!t.caps.enabled.has("soju.im/bouncer-networks"))return!1;let s={host:i};return"number"==typeof o&&(s.port=o),this.openDialog("network",{params:s,autojoin:e.entity}),!0}for(let[e,n]of this.state.servers)if(n.bouncerNetID===t){s=e;break}}else s=sO.getActiveServerID(this.state);if(!s)return!1;let l=sO.getBuffer(this.state,{server:s,name:e.entity||"*"});return l?this.switchBuffer(l.id):this.openDialog("confirm-open-buffer",{server:s,name:e.entity}),!0}handleNickClick(e){this.open(e)}whoUserBuffer(e,t){let s=this.clients.get(t);s.who(e,{fields:["flags","hostname","nick","realname","username","account"]}),s.monitor(e),s.supportsReadMarker()&&s.fetchReadMarker(e)}async whoChannelBuffer(e,t){let s=this.clients.get(t);this.setBufferState({name:e,server:t},{hasInitialWho:!0});let n=!1;try{await s.who(e,{fields:["flags","hostname","nick","realname","username","account"]}),n=!0}finally{this.setBufferState({name:e,server:t},{hasInitialWho:n})}}open(e,t,s){t||(t=sO.getActiveServerID(this.state));let n=this.clients.get(t);n.isServer(e)?this.switchBuffer({server:t}):n.isChannel(e)?(this.switchToChannel=e,n.join(e,s).catch(e=>{this.showError(e)})):(this.whoUserBuffer(e,t),this.createBuffer(t,e),this.switchBuffer({server:t,name:e}))}close(e){let t=sO.getBuffer(this.state,e);if(!t)return;let s=this.clients.get(t.server);switch(t.type){case sc:this.setState(e=>{let s=new Map(e.buffers);for(let[n,r]of e.buffers)r.server===t.server&&s.delete(n);let n=e.activeBuffer;return n&&e.buffers.get(n).server===t.server&&(n=s.size>0?s.keys().next().value:null),{buffers:s,activeBuffer:n}});let n=s&&!s.params.bouncerNetwork&&s.caps.enabled.has("soju.im/bouncer-networks"),r=this.state.servers.keys().next().value===t.server;if(this.disconnect(t.server),this.setState(e=>{let s=new Map(e.servers);s.delete(t.server);let n=e.connectForm;return 0===s.size&&(n=!0),{servers:s,connectForm:n}}),n){for(let e of this.clients.keys())this.close({server:e,name:"*"});this.bufferStore.clear()}else this.bufferStore.clear(s.params);r&&sM.put(null);break;case sh:t.joined&&s.send({command:"PART",params:[t.name]});case sp:this.state.activeBuffer===t.id&&this.switchBuffer({name:"*"}),this.setState(e=>{let s=new Map(e.buffers);return s.delete(t.id),{buffers:s}}),s.unmonitor(t.name),this.bufferStore.put({name:t.name,server:s.params,closed:!0})}}disconnectAll(){this.close(this.state.buffers.keys().next().value)}executeCommand(e){let t=e.split(" "),s=t[0].toLowerCase().slice(1),n=t.slice(1),r=nd.get(s);if(!r)return void this.showError(`Unknown command "${s}" (run "/help" to get a command list)`);try{r.execute(this,n)}catch(e){console.error(`Failed to execute command "${s}":`,e),this.showError(e.message)}}privmsg(e,t){if("*"===e)return void this.showError("Cannot send message in server buffer");let s=sO.getActiveServerID(this.state),n=this.clients.get(s),r={command:"PRIVMSG",params:[e,t]};n.send(r),n.caps.enabled.has("echo-message")||(r.prefix={name:n.nick},this.handleChatMessage(s,e,r))}handleComposerSubmit(e){if(!e)return;if(e.startsWith("//"))e=e.slice(1);else if(e.startsWith("/"))return void this.executeCommand(e);let t=this.state.buffers.get(this.state.activeBuffer);t&&this.privmsg(t.name,e)}handleBufferListClick(e){this.switchBuffer(e),this.closeBufferList()}handleBufferListClose(e){this.close(e),this.closeBufferList()}toggleBufferList(){this.setState(e=>({openPanels:{...e.openPanels,bufferList:!e.openPanels.bufferList}}))}toggleMemberList(){this.setState(e=>({openPanels:{...e.openPanels,memberList:!e.openPanels.memberList}}))}closeBufferList(){this.setState(e=>({openPanels:{...e.openPanels,bufferList:!1}}))}closeMemberList(){this.setState(e=>({openPanels:{...e.openPanels,memberList:!1}}))}handleJoinClick(e){switch(e.type){case sc:this.openDialog("join",{server:e.server});break;case sh:this.clients.get(e.server).send({command:"JOIN",params:[e.name]})}}handleJoinSubmit(e){this.open(e.channel,this.state.dialogData.server),this.dismissDialog()}handleConfirmOpenBufferSubmit(){this.open(this.state.dialogData.name,this.state.dialogData.server),this.dismissDialog()}autocomplete(e){function t(e,t){t=t.toLowerCase();let s=[];for(let n of e)n.toLowerCase().startsWith(t)&&s.push(n);return s}if(e.startsWith("/"))return t([...nd.keys()],e.slice(1)).map(e=>"/"+e);if(e.startsWith("#")){let s=[];for(let e of this.state.buffers.values())e.name.startsWith("#")&&s.push(e.name);return t(s,e)}let s=this.state.buffers.get(this.state.activeBuffer);return s&&s.members?t(s.members.keys(),e):[]}openHelp(){this.openDialog("help")}async handleBufferScrollTop(){let e,t=this.state.buffers.get(this.state.activeBuffer);if(!t||t.type===sc)return;let s=this.clients.get(t.server);if(!s||!s.caps.enabled.has("draft/chathistory")||!s.caps.enabled.has("server-time")||this.endOfHistory.get(t.id))return;e=t.messages.length>0?t.messages[0].tags.time:tW(new Date),this.endOfHistory.set(t.id,!0);let n=100;s.caps.enabled.has("draft/event-playback")&&(n=200);let r=await s.fetchHistoryBefore(t.name,e,n);if(this.endOfHistory.set(t.id,!r.more),r.messages.length>0){let e=r.messages[r.messages.length-1],n={[sf]:sS(e)};this.state.activeBuffer===t.id&&(n[sg]=sS(e));let a={name:t.name,server:s.params,receipts:n};this.bufferStore.put(a)&&this.sendReadReceipt(s,a),this.setBufferState(t,({prevReadReceipt:t})=>(s$(e,t)||(t=sS(e)),{prevReadReceipt:t}))}for(let e of r.messages)for(let s of(this.prepareChatMessage(t.server,e),this.routeMessage(t.server,e))){let n={server:t.server,name:s};this.setState(t=>sO.addMessage(t,e,n))}}openDialog(e,t){this.setState({dialog:e,dialogData:t})}dismissDialog(){this.setState({dialog:null,dialogData:null})}setDialogLoading(e){let t=e=>{this.setState(t=>({dialogData:{...t.dialogData,loading:e}}))};t(!0),e.finally(()=>t(!1))}handleAuthClick(e){let t=this.clients.get(e);this.openDialog("auth",{username:t.nick})}handleAuthSubmit(e,t){let s=sO.getActiveServerID(this.state),n=this.clients.get(s),r=n.authenticate("PLAIN",{username:e,password:t}).then(()=>{if(this.dismissDialog(),n!==this.clients.values().next().value)return;let s=sM.load();s&&(console.log("Saving SASL PLAIN credentials"),s={...s,saslPlain:{username:e,password:t}},sM.put(s))});this.setDialogLoading(r)}handleRegisterClick(e){let t=this.clients.get(e).checkAccountRegistrationCap("email-required");this.openDialog("register",{emailRequired:t})}handleRegisterSubmit(e,t){let s=sO.getActiveServerID(this.state),n=this.clients.get(s),r=n.registerAccount(e,t).then(e=>{if(this.dismissDialog(),e.verificationRequired&&this.handleVerifyClick(e.account,e.message),n!==this.clients.values().next().value)return;let s=sM.load();s&&(console.log("Saving account registration credentials"),s={...s,saslPlain:{username:e.account,password:t}},sM.put(s))});this.setDialogLoading(r)}handleVerifyClick(e,t){this.openDialog("verify",{account:e,message:t})}handleVerifySubmit(e){let t=sO.getActiveServerID(this.state),s=this.clients.get(t).verifyAccount(this.state.dialogData.account,e).then(()=>{this.dismissDialog()});this.setDialogLoading(s)}handleAddNetworkClick(){this.openDialog("network")}handleManageNetworkClick(e){let t=this.state.servers.get(e).bouncerNetID,s=this.state.bouncerNetworks.get(t);this.openDialog("network",{id:t,params:s})}async handleNetworkSubmit(e,t){let s=this.clients.values().next().value;if(this.dismissDialog(),this.state.dialogData&&this.state.dialogData.id){if(0===Object.keys(e).length)return;s.send({command:"BOUNCER",params:["CHANGENETWORK",this.state.dialogData.id,tO(e)]})}else{e={...e,tls:"1"};let n=await s.createBouncerNetwork(e);if(!t)return;let r=this.serverFromBouncerNetwork(n);this.clients.get(r).params.autojoin=[t],this.switchToChannel=t}}handleNetworkRemove(){this.clients.values().next().value.send({command:"BOUNCER",params:["DELNETWORK",this.state.dialogData.id]}),this.dismissDialog()}handleOpenSettingsClick(){let e=!1;for(let[t,s]of this.clients)if(s.caps.enabled.has("soju.im/bouncer-networks")){e=!0;break}this.openDialog("settings",{showProtocolHandler:e})}handleSettingsChange(e){sB.put(e),this.setState({settings:e})}handleSettingsDisconnect(){this.dismissDialog(),this.disconnectAll()}handleSwitchSubmit(e){this.dismissDialog(),e&&this.switchBuffer(e)}handleWindowFocus(){this.state.activeBuffer&&this.markBufferAsRead(this.state.activeBuffer);let e=new Date;if(!(this.lastFocusPingDate&&e.getTime()-this.lastFocusPingDate.getTime()<15e3))for(let t of(this.lastFocusPingDate=e,this.clients.values()))t.status===t8.Status.REGISTERED&&t.send({command:"PING",params:["gamja"]})}handleWindowHashChange(){let e;if(window.location.hash===this.computeWindowHash())return;let{host:t,entity:s}=nM(window.location.hash);if(t){let s=this.findBouncerNetIDByHost(t);if(!s)return void console.error(`No network found with host "${t}"`);for(let[t,n]of this.state.servers)if(n.bouncerNetID===s){e=t;break}}else for(let[t,s]of this.state.servers)if(!s.bouncerNetID){e=t;break}let n=sO.getBuffer(this.state,{server:e,name:s});n?this.switchBuffer(n.id):console.error(`No buffer found with entity "${s||"<none>"}"`)}componentDidMount(){var e;let t;this.baseTitle=document.title,e=this,t={},nr.forEach(e=>{t[e.key]||(t[e.key]=[]),t[e.key].push(e)}),window.addEventListener("keydown",s=>{let n=t[s.key];n&&1===(n=n.filter(e=>!!e.altKey===s.altKey&&!!e.ctrlKey===s.ctrlKey)).length&&(s.preventDefault(),n[0].execute(e))}),window.addEventListener("focus",this.handleWindowFocus),window.addEventListener("hashchange",this.handleWindowHashChange)}componentWillUnmount(){document.title=this.baseTitle,window.removeEventListener("focus",this.handleWindowFocus),window.removeEventListener("hashchange",this.handleWindowHashChange)}render(){let e,t;if(this.state.loading){let e=null;return this.state.error&&(e=tI`<form><p class="error-text">${this.state.error}</p></form>`),tI`<section id="connect">${e}</section>`}let s=null,n=null,r=null;if(this.state.buffers.get(this.state.activeBuffer)){s=this.state.buffers.get(this.state.activeBuffer);let e=(n=this.state.servers.get(s.server)).bouncerNetID;e&&(r=this.state.bouncerNetworks.get(e))}let a=null;if(s&&(a=this.clients.get(s.server)),this.state.connectForm){let e=n?n.status:sd.DISCONNECTED,t=e===sd.CONNECTING||e===sd.REGISTERING;return tI`
				<section id="connect">
					<${nt}
						error=${this.state.error}
						params=${this.state.connectParams}
						auth=${this.config.server.auth}
						connecting=${t}
						onSubmit=${this.handleConnectSubmit}
					/>
				</section>
			`}let i=null;if(s){let e=null;s.type===sp&&(e=n.users.get(s.name)),i=tI`
				<section id="buffer-header" role="banner">
					<${s6}
						buffer=${s}
						server=${n}
						user=${e}
						bouncerNetwork=${r}
						onChannelClick=${this.handleChannelClick}
						onClose=${()=>this.close(s)}
						onJoin=${()=>this.handleJoinClick(s)}
						onReconnect=${()=>this.reconnect()}
						onAddNetwork=${this.handleAddNetworkClick}
						onManageNetwork=${()=>this.handleManageNetworkClick(s.server)}
						onOpenSettings=${this.handleOpenSettingsClick}
					/>
				</section>
			`}let o=null;s&&s.type===sh&&(o=tI`
				<section
					id="member-list"
					class=${this.state.openPanels.memberList?"expand":""}
					role="complementary"
					aria-label="Members list"
				>
					<button
						class="expander"
						onClick=${this.toggleMemberList}
					>
						<span></span>
						<span></span>
					</button>
					<section>
						<section id="member-list-header">
							${s.members.size} users
						</section>
						<${s8}
							members=${s.members}
							users=${n.users}
							bouncerNetwork=${r}
							onNickClick=${this.handleNickClick}
						/>
					</section>
				</section>
			`);let l=null,u=this.state.dialogData||{};switch(this.state.dialog){case"network":let c=!u.id;l=tI`
				<${nD} title=${c?"Add network":"Edit network"} onDismiss=${this.dismissDialog}>
					<${nk}
						onSubmit=${this.handleNetworkSubmit}
						onRemove=${this.handleNetworkRemove}
						params=${u.params}
						autojoin=${u.autojoin}
						isNew=${c}
					/>
				</>
			`;break;case"help":l=tI`
				<${nD} title="Help" onDismiss=${this.dismissDialog}>
					<${ng}/>
				</>
			`;break;case"join":l=tI`
				<${nD} title="Join channel" onDismiss=${this.dismissDialog}>
					<${ns} channel=${u.channel} onSubmit=${this.handleJoinSubmit}/>
				</>
			`;break;case"confirm-open-buffer":let h=this.clients.get(u.server),p=this.state.servers.get(u.server),d=null;p.bouncerNetID&&(d=this.state.bouncerNetworks.get(p.bouncerNetID)),l=tI`
				<${nD} title="Open buffer">
					<${ne}
						name=${u.name}
						client=${h}
						server=${p}
						bouncerNetwork=${d}
						onSubmit=${this.handleConfirmOpenBufferSubmit}
					/>
				</>
			`;break;case"auth":e=u.loading?tI`<p>Logging in…</p>`:tI`
					<${nv} username=${u.username} onSubmit=${this.handleAuthSubmit}/>
				`,l=tI`
				<${nD} title="Login to ${sC(n,r)}" onDismiss=${this.dismissDialog}>
					${e}
				</>
			`;break;case"register":e=u.loading?tI`<p>Creating account…</p>`:tI`
					<${nw} emailRequired=${u.emailRequired} onSubmit=${this.handleRegisterSubmit}/>
				`,l=tI`
				<${nD} title="Register a new ${sC(n,r)} account" onDismiss=${this.dismissDialog}>
					${e}
				</>
			`;break;case"verify":e=u.loading?tI`<p>Verifying account…</p>`:tI`
					<${ny} account=${u.account} message=${u.message} onSubmit=${this.handleVerifySubmit}/>
				`,l=tI`
				<${nD} title="Verify ${sC(n,r)} account" onDismiss=${this.dismissDialog}>
					${e}
				</>
			`;break;case"settings":l=tI`
				<${nD} title="Settings" onDismiss=${this.dismissDialog}>
					<${nC}
						settings=${this.state.settings}
						showProtocolHandler=${u.showProtocolHandler}
						onChange=${this.handleSettingsChange}
						onDisconnect=${this.handleSettingsDisconnect}
						onClose=${this.dismissDialog}
					/>
				</>
			`;break;case"switch":l=tI`
				<${nD} title="Switch to a channel or user" onDismiss=${this.dismissDialog}>
					<${n$}
						buffers=${this.state.buffers}
						servers=${this.state.servers}
						bouncerNetworks=${this.state.bouncerNetworks}
						onSubmit=${this.handleSwitchSubmit}/>
				</>
			`}let m=null;this.state.error&&(m=tI`
				<div id="error-msg" role="alert">
					${this.state.error}
					${" "}
					<button onClick=${this.handleDismissError}>×</button>
				</div>
			`);let f=!1;n&&n.status!==sd.REGISTERED&&(f=!0);let g=!1;if(s&&s.type===sc)g=!0;else if(s){var b,k,v;let e,n=this.clients.get(s.server);b=n.isupport,k=n.nick,v=s.name,e=tM({prefix:{name:k,user:"_".repeat(b.userLen()),host:"_".repeat(b.hostLen())},command:"PRIVMSG",params:[v,""]})+"\r\n",t=b.lineLen()-e.length}let w=tI`
			<section
				id="buffer-list"
				class=${this.state.openPanels.bufferList?"expand":""}
			>
				<${s1}
					buffers=${this.state.buffers}
					servers=${this.state.servers}
					bouncerNetworks=${this.state.bouncerNetworks}
					activeBuffer=${this.state.activeBuffer}
					onBufferClick=${this.handleBufferListClick}
					onBufferClose=${this.handleBufferListClose}
				/>
				<button
					class="expander"
					onClick=${this.toggleBufferList}
				>
					<span></span>
					<span></span>
				</button>
			</section>
			${i}
			<${nR}
				target=${this.buffer}
				stickTo=".logline"
				scrollKey=${this.state.activeBuffer}
				onScrollTop=${this.handleBufferScrollTop}
			>
				<section id="buffer" ref=${this.buffer} tabindex="-1" role="log">
					<${sZ}
						buffer=${s}
						server=${n}
						bouncerNetwork=${r}
						settings=${this.state.settings}
						onChannelClick=${this.handleChannelClick}
						onNickClick=${this.handleNickClick}
						onAuthClick=${()=>this.handleAuthClick(s.server)}
						onRegisterClick=${()=>this.handleRegisterClick(s.server)}
						onVerifyClick=${this.handleVerifyClick}
					/>
				</section>
			</>
			${o}
			<${nx}
				ref=${this.composer}
				client=${a}
				readOnly=${f}
				onSubmit=${this.handleComposerSubmit}
				onError=${this.showError}
				autocomplete=${this.autocomplete}
				commandOnly=${g}
				maxLen=${t}
			/>
			${l}
			${m}
		`;return tI`
			<${sv.Provider} value=${this.state.settings}>
				${w}
			</>
		`}}t=tI`<${nB}/>`,(s=document.body)==document&&(s=document.documentElement),o.__&&o.__(t,s),n=s.__k,r=[],a=[],H(s,t=s.__k=I(T,null,[t]),n||y,y,s.namespaceURI,n?null:s.firstChild?i.call(s.childNodes):null,r,n?n.__e:s.firstChild,!1,a),j(r,t,a),t.props.children=null;
//# sourceMappingURL=gamja.eafc1c2e.js.map
