<template>
  <div class="flex flex-1 min-h-0 overflow-hidden">
    <Resizer :defaultWidth="240" :minWidth="160" :maxWidth="400" side="left" class="h-full">
      <BaileysConversationList :selectedJid="selectedJid" @select="onSelect" />
    </Resizer>
    <BaileysChat :jid="selectedJid" :displayName="selectedDisplayName" />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";
import Resizer from "@/components/Resizer.vue";

defineOptions({ inheritAttrs: false });

const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");

function onSelect(jid: string, displayName: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
}
</script>
