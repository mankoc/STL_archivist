// Meshes: flat shading with normals from screen-space derivatives (no normal buffer needed), a light kit in
// view space approximating VTK's, and Phong-style materials. Lit directly in gamma space, like VTK.

struct Globals {
    view: mat4x4<f32>,
    proj: mat4x4<f32>,
    background_top: vec4<f32>,
    background_bottom: vec4<f32>,
};
@group(0) @binding(0) var<uniform> globals: Globals;

struct Object {
    model: mat4x4<f32>,
    color: vec4<f32>,
    material: vec4<f32>, // ambient, diffuse, specular, specular power
};
@group(1) @binding(0) var<uniform> object: Object;

struct VertexOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) view_pos: vec3<f32>,
    @location(1) color: vec3<f32>,
};

fn project(position: vec3<f32>, color: vec3<f32>) -> VertexOut {
    let view_pos = globals.view * object.model * vec4<f32>(position, 1.0);
    var out: VertexOut;
    out.clip = globals.proj * view_pos;
    out.view_pos = view_pos.xyz;
    out.color = color;
    return out;
}

@vertex
fn vs_plain(@location(0) position: vec3<f32>) -> VertexOut {
    return project(position, object.color.rgb);
}

@vertex
fn vs_colored(@location(0) position: vec3<f32>, @location(1) rgba: vec4<f32>) -> VertexOut {
    return project(position, rgba.rgb);
}

// VTK's default light kit, in view space (camera looks down -Z, +Y up). Direction toward each light from
// (elevation e, azimuth a) relative to the camera: (sin a cos e, sin e, cos a cos e).
const KEY_DIR = vec3<f32>(0.1116, 0.7660, 0.6330);   // e = 50, a = 10
const FILL_DIR = vec3<f32>(-0.0449, -0.9659, 0.2549); // e = -75, a = -10
const BACK1_DIR = vec3<f32>(0.9397, 0.0, -0.3420);   // e = 0, a = 110
const BACK2_DIR = vec3<f32>(-0.9397, 0.0, -0.3420);  // e = 0, a = -110
const HEAD_DIR = vec3<f32>(0.0, 0.0, 1.0);
const KEY = 0.75;
const FILL = 0.25;  // key / 3
const BACK = 0.2143; // key / 3.5
const HEAD = 0.25;  // key / 3

// `l` must be a unit vector (the directions above are).
fn light(n: vec3<f32>, v: vec3<f32>, l: vec3<f32>, intensity: f32, power: f32) -> vec2<f32> {
    let diffuse = max(dot(n, l), 0.0) * intensity;
    let h = normalize(l + v);
    let specular = pow(max(dot(n, h), 0.0), power) * intensity;
    return vec2<f32>(diffuse, specular);
}

@fragment
fn fs_lit(in: VertexOut) -> @location(0) vec4<f32> {
    var n = normalize(cross(dpdx(in.view_pos), dpdy(in.view_pos)));
    let v = normalize(-in.view_pos);
    if dot(n, v) < 0.0 {
        n = -n; // two-sided lighting, as VTK
    }
    let m = object.material;
    let power = max(m.w, 1.0);
    let lit = light(n, v, KEY_DIR, KEY, power) + light(n, v, FILL_DIR, FILL, power)
        + light(n, v, BACK1_DIR, BACK, power) + light(n, v, BACK2_DIR, BACK, power)
        + light(n, v, HEAD_DIR, HEAD, power);
    let rgb = m.x * in.color + m.y * lit.x * in.color + m.z * lit.y * vec3<f32>(1.0);
    return vec4<f32>(clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.0)), 1.0);
}

@fragment
fn fs_wire(in: VertexOut) -> @location(0) vec4<f32> {
    return vec4<f32>(in.color, 1.0);
}
