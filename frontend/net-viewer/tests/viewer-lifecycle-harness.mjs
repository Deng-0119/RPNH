// Small JointJS/ResizeObserver doubles for running the real NetRenderer and app
// startup without a browser. These do not simulate browser paint or bfcache itself.
export function rendererRuntime(document, window) {
    class Cell {
        constructor(attributes) {this.attributes=attributes;this.labels=[];}
        get(key) {return this.attributes[key];}
        attr(key,value) {this.attributes.attrs[key]=value;}
        appendLabel(value) {this.labels.push(value);}
        label(index,value) {this.labels[index]=value;}
    }
    class Graph {
        resetCells(cells) {this.cells=new Map(cells.map(cell=>[cell.get('id'),cell]));}
        getCell(id) {return this.cells?.get(id);}
    }
    class Paper {
        constructor(options) {
            this.options=options;this.el=options.el;this.svg=document.createElement('svg');
            this.el.append(this.svg);this.views=new Map();this.scaleValue=1;this.position={tx:0,ty:0};
            this.removed=false;
        }
        on() {}
        findViewByModel(cell) {
            if(!this.views.has(cell)) {const el=document.createElement('g');this.svg.append(el);this.views.set(cell,{el});}
            return this.views.get(cell);
        }
        scale(value) {if(value!==undefined)this.scaleValue=value;return {sx:this.scaleValue};}
        translate(tx,ty) {if(tx!==undefined)this.position={tx,ty};return this.position;}
        setDimensions(width,height) {Object.assign(this.options,{width,height});}
        remove() {this.removed=true;this.el.remove();}
    }
    class ResizeObserver {
        constructor(callback) {this.callback=callback;this.connected=false;}
        observe(target) {this.target=target;this.connected=true;}
        disconnect() {this.connected=false;}
        notify() {if(this.connected)this.callback();}
    }
    window.matchMedia=()=>({matches:false});
    window.ELK=class {};
    window.joint={dia:{Graph,Paper,Element:{define:()=>Cell}},shapes:{standard:{Link:Cell}}};
    return {ResizeObserver};
}
