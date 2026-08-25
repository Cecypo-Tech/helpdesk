<template>
  <div class="flex flex-col">
    <div
      v-if="!backupPortalEnabled"
      class="flex flex-1 items-center justify-center p-8 text-center text-p-sm text-ink-gray-6"
    >
      {{ __("The Imara Backup app is not installed on this site.") }}
    </div>
    <!--
      The portal is a set of server-rendered Frappe web pages, not part of this
      SPA, so it is embedded rather than routed to. It carries its own tab row
      for Agents / Events / Restore / Plan / Notifications, which is why this
      page adds no header of its own -- a LayoutHeader here would sit directly
      above that row and read as two navigations for one section.

      No ?embed=1 on the src: the portal decides for itself whether it is framed
      (see imara_backup templates/includes/embed_head.html) and hides the website
      chrome accordingly. That keeps intra-portal links working untouched, so
      clicking through to Plan or Restore inside the frame stays chrome-free.
    -->
    <iframe
      v-else
      :src="PORTAL_URL"
      class="w-full flex-1 border-0"
      :title="__('Imara Cloud Backup')"
    />
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from "pinia";
import { useConfigStore } from "@/stores/config";
import { __ } from "@/translation";

const PORTAL_URL = "/backup";

const { backupPortalEnabled } = storeToRefs(useConfigStore());
</script>
