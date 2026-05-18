<template>
  <div ref="containerRef" class="flex flex-1 min-h-0 overflow-hidden">
    <Resizer :defaultWidth="240" :minWidth="160" :maxWidth="400" side="left" :parent="containerRef" class="h-full">
      <BaileysConversationList
        ref="convListRef"
        :selectedJid="selectedJid"
        @select="onSelect"
      />
    </Resizer>
    <BaileysChat
      :jid="selectedJid"
      :displayName="selectedDisplayName"
      :company="selectedCompany"
      @contactSaved="onContactSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";
import Resizer from "@/components/Resizer.vue";

defineOptions({ inheritAttrs: false });

const containerRef = ref<HTMLElement | null>(null);
const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");
const selectedCompany = ref<string>("");
const convListRef = ref<InstanceType<typeof BaileysConversationList> | null>(null);

function onSelect(jid: string, displayName: string, company: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
  selectedCompany.value = company;
}

function onContactSaved(data: { custom_name: string; company: string }) {
  if (data.custom_name) selectedDisplayName.value = data.custom_name;
  selectedCompany.value = data.company;
  convListRef.value?.reload();
}
</script>
