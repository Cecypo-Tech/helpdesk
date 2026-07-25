import vue from "@vitejs/plugin-vue";
import vueJsx from "@vitejs/plugin-vue-jsx";
import frappeui from "frappe-ui/vite";
import path from "path";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    frappeui({
      frappeProxy: true,
      lucideIcons: true,
      jinjaBootData: true,
      buildConfig: {
        outDir: `../helpdesk/public/desk`,
        emptyOutDir: true,
        indexHtmlPath: "../helpdesk/www/helpdesk/index.html",
      },
      frappeTypes: {
        input: {
          helpdesk: [
            "hd_ticket_status",
            "hd_ticket",
            "hd_service_holiday_list",
            "hd_service_level_agreement",
            "hd_agent",
          ],
          frappe: ["assignment_rule"],
        },
      },
    }),

    vue(),
    vueJsx(),
    VitePWA({
      registerType: "autoUpdate",
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.js",
      // The generated registerSW.js registers the worker at the path it was
      // built into (/assets/helpdesk/desk/), whose scope cannot reach the app
      // at /helpdesk/. Registration is done by hand in src/pwa.ts against
      // /helpdesk/sw.js instead — see helpdesk/service_worker.py.
      injectRegister: false,
      devOptions: {
        enabled: true,
        type: "module",
      },
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg}"],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
      manifest: {
        display: "standalone",
        name: "Frappe Helpdesk",
        short_name: "Helpdesk",
        // start_url must sit inside scope or the manifest is invalid and the
        // install prompt never qualifies. Neither takes a trailing slash:
        // "/helpdesk/" 301-redirects to "/helpdesk", so a slashed start_url
        // would launch the installed app straight out of its own scope. Scope
        // matching is a plain path-prefix test, so "/helpdesk" covers both the
        // entry point and every route beneath it.
        start_url: "/helpdesk",
        scope: "/helpdesk",
        theme_color: "#ffffff",
        background_color: "#ffffff",
        description:
          "Modern, Streamlined, Free and Open Source Customer Service Software",
        // "any" and "maskable" are not interchangeable. Maskable art keeps its
        // logo inside the central safe circle so a launcher can crop it to any
        // shape; handing that same file to "any", as this used to, renders a
        // small logo adrift in its background wherever the full square is shown
        // (desktop, iOS). The plain icons are the same art with the safe-zone
        // padding cropped off.
        icons: [
          {
            src: "/assets/helpdesk/desk/manifest/manifest-icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/assets/helpdesk/desk/manifest/manifest-icon-192.maskable.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "maskable",
          },
          {
            src: "/assets/helpdesk/desk/manifest/manifest-icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/assets/helpdesk/desk/manifest/manifest-icon-512.maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
    }),
  ],
  server: {
    allowedHosts: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "tailwind.config.js": path.resolve(__dirname, "tailwind.config.js"),
    },
  },
  optimizeDeps: {
    include: [
      "feather-icons",
      "tailwind.config.js",
      "prosemirror-state",
      "prosemirror-view",
      "lowlight",
      "interactjs",
    ],
    exclude: ["frappe-ui"],
  },
});
