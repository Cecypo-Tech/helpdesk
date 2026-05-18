<template>
  <div class="flex h-full overflow-hidden">
    <BaileysConversationList
      :selectedJid="selectedJid"
      @select="onSelect"
    />
    <BaileysChat :jid="selectedJid" :displayName="selectedDisplayName" />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";

const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");

function onSelect(jid: string, displayName: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
}
</script>
