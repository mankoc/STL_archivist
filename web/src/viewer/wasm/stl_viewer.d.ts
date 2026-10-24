/* tslint:disable */
/* eslint-disable */

/**
 * What [`Viewer::add`] put in the view.
 */
export class Loaded {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Objects (models and supports).
     */
    readonly objects: number;
    /**
     * Triangles in all objects.
     */
    readonly triangles: number;
    /**
     * `"<name>: <reason>"` for the objects the file has but could not be loaded.
     */
    readonly skipped: string[];
}

/**
 * A canvas showing one or more scenes.
 */
export class Viewer {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Show the file `key` (a scene from `GET /api/models/{id}/view`), replacing what it showed before. The
     * first file shown is seen from the isometric view; later ones are framed with everything, keeping the
     * viewing direction.
     */
    add(key: string, data: Uint8Array): Loaded;
    /**
     * The camera's right, up and viewing directions in world coordinates (9 numbers), for the view cube.
     */
    camera_basis(): Float64Array;
    /**
     * Draw into `canvas` (sized by the page) with WebGPU when the browser has it, otherwise WebGL2;
     * `webgl` forces WebGL2. A canvas WebGPU failed on cannot be used for WebGL2: retry on a new one.
     */
    static create(canvas: HTMLCanvasElement, webgl: boolean): Promise<Viewer>;
    /**
     * Frame everything.
     */
    fit(): void;
    /**
     * Frame the file's objects.
     */
    focus(key: string): void;
    /**
     * Turntable orbit: `azimuth` about the world Z axis, `elevation` toward the poles, in degrees.
     */
    orbit(azimuth: number, elevation: number): void;
    /**
     * Move the view by canvas pixels.
     */
    pan(dx: number, dy: number): void;
    /**
     * Stop showing the file `key`.
     */
    remove(key: string): void;
    /**
     * Draw a frame.
     */
    render(): void;
    /**
     * The canvas is now `width` x `height` pixels.
     */
    resize(width: number, height: number): void;
    /**
     * Give each model its own colour (MeshPeek's "Colour each model"); colours set per file still win.
     */
    set_color_each(on: boolean): void;
    /**
     * Follow the page's theme (background and plate colours).
     */
    set_dark(dark: boolean): void;
    /**
     * Show or hide the build plates and volumes of slicer scenes.
     */
    set_show_plates(show: boolean): void;
    /**
     * Draw the file's models in `color` (`#rrggbb`; `None` or anything else: as by default), with one of
     * MeshPeek's materials (`Matte`, `Plastic`, `Glossy` or `Metal`; others keep the current one), as its
     * edges (`wire`) or as surfaces. Only this file is restyled.
     */
    set_style(key: string, color: string | null | undefined, material: string, wire: boolean): void;
    /**
     * Show or hide the file (it stays loaded).
     */
    set_visible(key: string, on: boolean): void;
    /**
     * Look along a standard view (`isometric`, `front`, `back`, `left`, `right`, `top`, `bottom`) and frame
     * everything; other names are ignored.
     */
    show_view(name: string): void;
    /**
     * Move toward (`factor` > 1) or away from the focal point.
     */
    zoom(factor: number): void;
    /**
     * "WebGPU" or "WebGL2".
     */
    readonly backend: string;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_get_loaded_objects: (a: number) => number;
    readonly __wbg_get_loaded_triangles: (a: number) => number;
    readonly __wbg_loaded_free: (a: number, b: number) => void;
    readonly __wbg_viewer_free: (a: number, b: number) => void;
    readonly loaded_skipped: (a: number) => [number, number];
    readonly viewer_add: (a: number, b: number, c: number, d: number, e: number) => [number, number, number];
    readonly viewer_backend: (a: number) => [number, number];
    readonly viewer_camera_basis: (a: number) => [number, number];
    readonly viewer_create: (a: any, b: number) => any;
    readonly viewer_fit: (a: number) => void;
    readonly viewer_focus: (a: number, b: number, c: number) => void;
    readonly viewer_orbit: (a: number, b: number, c: number) => void;
    readonly viewer_pan: (a: number, b: number, c: number) => void;
    readonly viewer_remove: (a: number, b: number, c: number) => void;
    readonly viewer_render: (a: number) => [number, number];
    readonly viewer_resize: (a: number, b: number, c: number) => void;
    readonly viewer_set_color_each: (a: number, b: number) => void;
    readonly viewer_set_dark: (a: number, b: number) => void;
    readonly viewer_set_show_plates: (a: number, b: number) => void;
    readonly viewer_set_style: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number) => void;
    readonly viewer_set_visible: (a: number, b: number, c: number, d: number) => void;
    readonly viewer_show_view: (a: number, b: number, c: number) => void;
    readonly viewer_zoom: (a: number, b: number) => void;
    readonly wasm_bindgen_dac0731650b473ce___convert__closures_____invoke___js_sys_a89202e6452953cf___Function_fn_wasm_bindgen_dac0731650b473ce___JsValue_____wasm_bindgen_dac0731650b473ce___sys__Undefined___js_sys_a89202e6452953cf___Function_fn_wasm_bindgen_dac0731650b473ce___JsValue_____wasm_bindgen_dac0731650b473ce___sys__Undefined_______true_: (a: number, b: number, c: any, d: any) => void;
    readonly wasm_bindgen_dac0731650b473ce___convert__closures_____invoke___wasm_bindgen_dac0731650b473ce___JsValue__core_608f92abc48d28da___result__Result_____wasm_bindgen_dac0731650b473ce___JsError___true_: (a: number, b: number, c: any) => [number, number];
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_exn_store: (a: number) => void;
    readonly __externref_table_alloc: () => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_destroy_closure: (a: number, b: number) => void;
    readonly __externref_drop_slice: (a: number, b: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
