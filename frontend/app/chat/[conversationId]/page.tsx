import {ChatWorkspace} from "@/components/chat-workspace";

export default async function ConversationPage({params}: {params: Promise<{conversationId: string}>}) {
  const {conversationId} = await params;
  return <ChatWorkspace conversationId={conversationId} />;
}
