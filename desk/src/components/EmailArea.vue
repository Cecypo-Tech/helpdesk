<template>
  <div
    :id="`communication-${name}`"
    v-bind="$attrs"
    class="email-activity-card grow cursor-pointer border-transparent bg-surface-white rounded-md shadow text-base leading-6 transition-all duration-300 ease-in-out"
  >
    <div
      class="flex items-center justify-between gap-2"
      :class="isMobileView && 'items-start'"
    >
      <!-- email design for mobile -->
      <div v-if="isMobileView" class="flex items-center gap-2 text-sm">
        <div class="leading-tight">
          <p>{{ sender.full_name || "Guest" }}</p>
          <Tooltip :text="dateFormat(creation, dateTooltipFormat)">
            <p class="text-xs md:text-sm text-ink-gray-6">
              {{ timeAgo(creation) }}
            </p>
          </Tooltip>
          <p class="sm:flex hidden text-sm text-ink-gray-6" v-if="sender.name">
            {{ "<" + sender.name + ">" }}
          </p>
        </div>
      </div>
      <!-- email design for desktop -->
      <div v-else class="flex items-center gap-1">
        <span>{{ sender.full_name || "Guest" }}</span>
        <span class="sm:flex hidden text-sm text-ink-gray-6" v-if="sender.name">{{
          "<" + sender.name + ">"
        }}</span>
      </div>

      <div class="flex gap-0.5 items-center">
        <Badge
          v-if="status.label"
          :label="__(status.label)"
          variant="subtle"
          :theme="status.color"
          class="mr-1.5"
        />
        <Tooltip
          :text="dateFormat(creation, dateTooltipFormat)"
          v-if="!isMobileView"
        >
          <p class="text-xs md:text-sm text-ink-gray-6">
            {{ timeAgo(creation) }}
          </p>
        </Tooltip>
        <Button variant="ghost" class="text-ink-gray-7" @click="reply">
          <ReplyIcon class="h-4 w-4" />
        </Button>
        <Button variant="ghost" class="text-ink-gray-7" @click="replyAll">
          <ReplyAllIcon class="h-4 w-4" />
        </Button>
        <Dropdown
          v-if="showSplitOption"
          :placement="'right'"
          :options="[
            {
              label: 'Split Ticket',
              icon: LucideSplit,
              onClick: () => (showSplitModal = true),
            },
          ]"
        >
          <Button
            icon="more-horizontal"
            class="text-ink-gray-6"
            variant="ghost"
          />
        </Dropdown>
      </div>
    </div>
    <!-- <div class="text-sm leading-5 text-ink-gray-6">
      {{ subject }}
    </div> -->
    <div class="text-sm leading-5 text-ink-gray-6">
      <span v-if="to" class="text-2xs mr-1 font-bold text-ink-gray-5">TO:</span>
      <span v-if="to"> {{ to }} </span>
      <span v-if="cc">, </span>
      <span v-if="cc" class="text-2xs mr-1 font-bold text-ink-gray-5"> CC: </span>
      <span v-if="cc">{{ cc }}</span>
      <span v-if="bcc">, </span>
      <span v-if="bcc" class="text-2xs mr-1 font-bold text-ink-gray-5">
        BCC:
      </span>
      <span v-if="bcc">{{ bcc }}</span>
    </div>
    <div class="border-0 border-t my-3 border-outline-gray-modals" />
    <EmailContent :content="content" />
    <div class="flex flex-wrap gap-2">
      <AttachmentItem
        v-for="a in attachments"
        :key="a.file_url"
        :label="a.file_name"
        :url="a.file_url"
      />
    </div>
  </div>
  <TicketSplitModal
    v-model="showSplitModal"
    :ticket_id="name"
    :communication_id="name"
  />
</template>

<script setup lang="ts">
import { AttachmentItem } from "@/components";
import { useScreenSize } from "@/composables/screen";
import { dateFormat, dateTooltipFormat, timeAgo } from "@/utils";
import { Dropdown } from "frappe-ui";
import { computed, ref } from "vue";
import LucideSplit from "~icons/lucide/split";
import { ReplyAllIcon, ReplyIcon } from "./icons";
import TicketSplitModal from "./ticket/TicketSplitModal.vue";
import { useAuthStore } from "@/stores/auth";
import { storeToRefs } from "pinia";

const props = defineProps({
  activity: {
    type: Object,
    required: true,
  },
  showSplitOption: {
    type: Boolean,
    default: false,
  },
});

const {
  sender,
  to,
  cc,
  bcc,
  creation,
  subject,
  attachments,
  content,
  name,
  deliveryStatus,
} = props.activity;

const emit = defineEmits(["reply"]);

const auth = storeToRefs(useAuthStore());

const { isMobileView } = useScreenSize();

const showSplitModal = ref(false);

const status = computed(() => {
  let _status = deliveryStatus;
  let indicator_color = "red";
  if (["Sent", "Clicked"].includes(_status)) {
    indicator_color = "green";
  } else if (["Sending", "Scheduled"].includes(_status)) {
    indicator_color = "orange";
  } else if (["Opened", "Read"].includes(_status)) {
    indicator_color = "blue";
  } else if (_status == "Error") {
    indicator_color = "red";
  }
  return { label: _status, color: indicator_color };
});

const reply = () => {
  const user = auth.user.value;
  emit("reply", {
    content: content,
    to: user === sender.name ? to : sender.name,
  });
};

const replyAll = () => {
  const user = auth.user.value;

  const normalizeAndFilter = (field) => {
    let arr = [];
    let current = "";
    let inQuotes = false;
    if (typeof field === "string") {
      for (let char of field) {
        if (char === '"') {
          inQuotes = !inQuotes;
          current += char;
        } else if (char === "," && !inQuotes) {
          arr.push(current.trim());
          current = "";
        } else {
          current += char;
        }
      }
      if (current) arr.push(current.trim());
    } else {
      arr = field || [];
    }
    return arr.filter((item) => item !== user && item !== sender.name);
  };

  const filteredTo = normalizeAndFilter(to);
  const filteredCc = normalizeAndFilter(cc);
  const filteredBcc = normalizeAndFilter(bcc);

  let _to, _cc, _bcc;

  if (user === sender.name) {
    // User is the sender, reply to all original recipients
    _to = filteredTo.join(", ");
    _cc = filteredCc;
    _bcc = filteredBcc;
  } else {
    // User is a recipient, reply to sender with all other recipients in cc
    _to = sender.name;
    _cc = [...filteredTo, ...filteredCc];
    _bcc = filteredBcc;
  }

  emit("reply", {
    content: content,
    to: _to,
    cc: _cc.filter(Boolean),
    bcc: _bcc.filter(Boolean),
  });
};

// TODO: Implement reply functionality using this way instead of emit drillup
// function reply(email, reply_all = false) {
//   emailBox.toggleEmailBox();
//   let editor = emailBox.editor;
//   let message = email.content;
//   let recipients = sender.name;
//   editor.toEmails = [email.sender];
//   editor.cc = editor.bcc = false;
//   editor.ccEmails = [];
//   editor.bccEmails = [];
//   console.log(recipients);

//   if (!email.subject.startsWith("Re:")) {
//     editor.subject = `Re: ${email.subject}`;
//   } else {
//     editor.subject = email.subject;
//   }

//   if (reply_all) {
//     let cc = email.cc?.split(",").map((r) => r.trim());
//     let bcc = email.bcc?.split(",").map((r) => r.trim());

//     if (cc?.length) {
//       recipients = recipients.filter((r) => !cc?.includes(r));
//       cc.push(...recipients);
//     } else {
//       cc = recipients;
//     }

//     editor.cc = cc ? true : false;
//     editor.bcc = bcc ? true : false;

//     editor.ccEmails = cc;
//     editor.bccEmails = bcc;
//   }

//   let repliedMessage = `<blockquote>${message}</blockquote>`;

//   editor.editor
//     .chain()
//     .clearContent()
//     .insertContent("<p>.</p>")
//     .updateAttributes("paragraph", { class: "reply-to-content" })
//     .insertContent(repliedMessage)
//     .focus("all")
//     .insertContentAt(0, { type: "paragraph" })
//     .focus("start")
//     .run();
// }
</script>

<style>
.email-content {
  max-width: 100%;
}
.email-content > * {
  display: flex;
  flex-direction: column;
  flex-wrap: nowrap;
}

/* Email activity cards render actual email content (via EmailContent.vue's
   iframe, forced to a fixed light background/text since sender HTML assumes
   one). Pin this card's own chrome — header, sender line, divider, badges,
   attachments — to the same light-mode token values so the whole card reads
   as one consistent light surface instead of a dark header on a white body. */
.email-activity-card{--outline-white: #FFFFFF;--outline-gray-1: #EDEDED;--outline-gray-2: #E2E2E2;--outline-gray-3: #C7C7C7;--outline-gray-4: #999999;--outline-gray-5: #383838;--outline-red-1: #FDC2C2;--outline-red-2: #F79596;--outline-red-3: #E03636;--outline-green-1: #A6EFC0;--outline-green-2: #86E0A8;--outline-amber-1: #FBDB73;--outline-amber-2: #FBCC55;--outline-blue-1: #A7D7FD;--outline-orange-1: #F4B07F;--outline-gray-modals: #EDEDED;--surface-white: #FFFFFF;--surface-gray-1: #F8F8F8;--surface-gray-2: #F3F3F3;--surface-gray-3: #EDEDED;--surface-gray-4: #E2E2E2;--surface-gray-5: #525252;--surface-gray-6: #383838;--surface-gray-7: #171717;--surface-red-1: #FFF7F7;--surface-red-2: #FFE7E7;--surface-red-3: #FFD8D8;--surface-red-4: #FDC2C2;--surface-red-5: #CC2929;--surface-red-6: #B52A2A;--surface-red-7: #941F1F;--surface-green-1: #F2FDF4;--surface-green-2: #E4FAEB;--surface-green-3: #278F5E;--surface-amber-1: #FDFAED;--surface-amber-2: #FFF7D3;--surface-amber-3: #DB7706;--surface-blue-1: #F2F9FF;--surface-blue-2: #E6F4FF;--surface-blue-3: #007BE0;--surface-orange-1: #FFEFE4;--surface-violet-1: #F0EBFF;--surface-cyan-1: #DDF7FF;--surface-pink-1: #FDE8F5;--surface-menu-bar: #F8F8F8;--surface-cards: #FFFFFF;--surface-modal: #FFFFFF;--surface-selected: #FFFFFF;--ink-white: #FFFFFF;--ink-gray-1: #EDEDED;--ink-gray-2: #E2E2E2;--ink-gray-3: #C7C7C7;--ink-gray-4: #999999;--ink-gray-5: #7C7C7C;--ink-gray-6: #525252;--ink-gray-7: #525252;--ink-gray-8: #383838;--ink-gray-9: #171717;--ink-red-1: #FFF7F7;--ink-red-2: #F79596;--ink-red-3: #E03636;--ink-red-4: #CC2929;--ink-green-1: #F2FDF4;--ink-green-2: #46B37E;--ink-green-3: #278F5E;--ink-amber-1: #FDFAED;--ink-amber-2: #E79913;--ink-amber-3: #DB7706;--ink-blue-1: #F2F9FF;--ink-blue-2: #0289F7;--ink-blue-3: #007BE0;--ink-cyan-1: #3BBDE5;--ink-pink-1: #E34AA6;--ink-violet-1: #6846E3;--ink-blue-link: #73BBF6;--gray-50: #F8F8F8;--gray-100: #F3F3F3;--gray-200: #EDEDED;--gray-300: #E2E2E2;--gray-400: #C7C7C7;--gray-500: #999999;--gray-600: #7C7C7C;--gray-700: #525252;--gray-800: #383838;--gray-900: #171717;--blue-50: #F2F9FF;--blue-100: #E6F4FF;--blue-200: #C8E6FF;--blue-300: #A7D7FD;--blue-400: #73BBF6;--blue-500: #0289F7;--blue-600: #007BE0;--blue-700: #0070CC;--blue-800: #005CA3;--blue-900: #004880;--green-50: #F2FDF4;--green-100: #E4FAEB;--green-200: #C3F9D3;--green-300: #A6EFC0;--green-400: #86E0A8;--green-500: #46B37E;--green-600: #278F5E;--green-700: #137949;--green-800: #075E35;--green-900: #173B2C;--red-50: #FFF7F7;--red-100: #FFE7E7;--red-200: #FFD8D8;--red-300: #FDC2C2;--red-400: #F79596;--red-500: #E03636;--red-600: #CC2929;--red-700: #B52A2A;--red-800: #941F1F;--red-900: #6B1515;--amber-50: #FDFAED;--amber-100: #FFF7D3;--amber-200: #FEEDA9;--amber-300: #FBDB73;--amber-400: #FBCC55;--amber-500: #E79913;--amber-600: #DB7706;--amber-700: #B35309;--amber-800: #91400D;--amber-900: #763813;--orange-50: #FFF9F5;--orange-100: #FFEFE4;--orange-200: #FFDEC5;--orange-300: #FFCBA3;--orange-400: #F4B07F;--orange-500: #E86C13;--orange-600: #D45A08;--orange-700: #BD3E0C;--orange-800: #9E3513;--orange-900: #6B2711;--yellow-50: #FFFCEF;--yellow-100: #FFF7D3;--yellow-200: #F7E9A8;--yellow-300: #F5E171;--yellow-400: #F2D14B;--yellow-500: #EDBA13;--yellow-600: #D1930D;--yellow-700: #AB6E05;--yellow-800: #8C5600;--yellow-900: #733F12;--teal-50: #F0FDFA;--teal-100: #E6F7F4;--teal-200: #BAE8E1;--teal-300: #97DED4;--teal-400: #73D1C4;--teal-500: #36BAAD;--teal-600: #0B9E92;--teal-700: #0F736B;--teal-800: #115C57;--teal-900: #114541;--cyan-50: #F5FBFC;--cyan-100: #DDF7FF;--cyan-200: #B3E8F7;--cyan-300: #99E2F8;--cyan-400: #72D5F3;--cyan-500: #3BBDE5;--cyan-600: #32A4C7;--cyan-700: #267A94;--cyan-800: #125C73;--cyan-900: #164759;--purple-50: #FDFAFF;--purple-100: #F6E9FF;--purple-200: #ECD3FF;--purple-300: #E2B9FC;--purple-400: #CFA1F2;--purple-500: #9C45E3;--purple-600: #8642C2;--purple-700: #6E399D;--purple-800: #5C2F83;--purple-900: #401863;--pink-50: #FFF7FC;--pink-100: #FDE8F5;--pink-200: #FFD5F0;--pink-300: #F9B9E0;--pink-400: #F6A7D6;--pink-500: #E34AA6;--pink-600: #CF3A96;--pink-700: #9C2671;--pink-800: #801458;--pink-900: #570F3E;--violet-50: #FBFAFF;--violet-100: #F0EBFF;--violet-200: #DBD5FF;--violet-300: #C9BAFB;--violet-400: #B3A1F5;--violet-500: #6846E3;--violet-600: #5F46C7;--violet-700: #4F3DA1;--violet-800: #392980;--violet-900: #251959}
</style>
