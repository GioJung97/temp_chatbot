"use client";

import { useEffect, useMemo, useState } from "react";
import { ChatWindow } from "./components/ChatWindow";
import { Composer } from "./components/Composer";
import { ThemeToggle } from "./components/ThemeToggle";
import type {
  ChatHistoryResponse,
  ChatMessage,
  ChatResponse,
  ConversationsResponse,
  ConversationSummary,
  ComposerImage
} from "./types/chat";

const MAX_IMAGE_SIZE = 5 * 1024 * 1024;
export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [value, setValue] = useState("");
  const [image, setImage] = useState<ComposerImage | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const hasMessages = messages.length > 0;

  const canSend = useMemo(
    () => value.trim().length > 0 || Boolean(image),
    [value, image]
  );

  useEffect(() => {
    void loadConversations();
  }, []);

  const loadConversations = async () => {
    try {
      const response = await fetch("/api/conversations");
      if (!response.ok) return;
      const data = (await response.json()) as ConversationsResponse;
      setConversations(data.conversations);
    } catch {
      // ignore history load failures
    }
  };

  const loadConversation = async (id: string) => {
    setIsLoading(true);
    setError(undefined);

    try {
      const response = await fetch(`/api/chat?conversationId=${id}`);
      if (response.status === 404) {
        setConversationId(null);
        setMessages([]);
        return;
      }

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.error ?? "Failed to load conversation.");
        return;
      }

      const data = (await response.json()) as ChatHistoryResponse;
      setMessages(data.messages);
      setConversationId(data.conversationId);
    } catch (err) {
      setError("Failed to load conversation.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleImageSelected = (file: File) => {
    if (file.size > MAX_IMAGE_SIZE) {
      setError("Image is too large. Please select a file under 5MB.");
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") {
        setImage({ name: file.name, dataUrl: result, file });
        setError(undefined);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleRemoveImage = () => {
    setImage(undefined);
    setError(undefined);
  };

  const handleSend = async () => {
    if (!canSend || isSending) {
      return;
    }

    const trimmed = value.trim();
    const now = Date.now();
    const tempUserId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `user-${now}`;
    const tempAssistantId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `assistant-${now}`;

    const optimisticUser: ChatMessage = {
      id: tempUserId,
      role: "user",
      text: trimmed || null,
      imageUrl: image?.dataUrl,
      imageName: image?.name ?? null,
      createdAt: now
    };

    const optimisticAssistant: ChatMessage = {
      id: tempAssistantId,
      role: "assistant",
      text: "Loading...",
      createdAt: now + 1,
      isPending: true
    };

    const formData = new FormData();
    if (trimmed) {
      formData.append("text", trimmed);
    }
    if (image) {
      formData.append("image", image.file);
    }
    if (conversationId) {
      formData.append("conversationId", conversationId);
    }

    setIsSending(true);
    setError(undefined);
    setMessages((prev) => [...prev, optimisticUser, optimisticAssistant]);
    setValue("");
    setImage(undefined);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.error ?? "Failed to send message.");
        setMessages((prev) =>
          prev.map((message) => {
            if (message.id === tempAssistantId) {
              return {
                ...message,
                text: "There was an error generating a response. Please try again.",
                isPending: false
              };
            }
            return message;
          })
        );
        return;
      }

      const data = (await response.json()) as ChatResponse;
      setConversationId(data.conversationId);
      setMessages((prev) => {
        let replacedUser = false;
        let replacedAssistant = false;
        const next = prev.map((message) => {
          if (message.id === tempUserId) {
            replacedUser = true;
            return data.userMessage;
          }
          if (message.id === tempAssistantId) {
            replacedAssistant = true;
            return data.assistantMessage;
          }
          return message;
        });

        if (!replacedUser) {
          next.push(data.userMessage);
        }
        if (!replacedAssistant) {
          next.push(data.assistantMessage);
        }
        return next;
      });
      void loadConversations();
    } catch (err) {
      setError("Failed to send message.");
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id === tempAssistantId) {
            return {
              ...message,
              text: "There was an error generating a response. Please try again.",
              isPending: false
            };
          }
          return message;
        })
      );
    } finally {
      setIsSending(false);
    }
  };

  const handleStartNewChat = () => {
    setConversationId(null);
    setMessages([]);
    setValue("");
    setImage(undefined);
    setError(undefined);
  };

  const handleSelectConversation = (id: string) => {
    setIsSidebarOpen(false);
    void loadConversation(id);
  };

  return (
    <div className="flex min-h-screen flex-col bg-ink-50 transition-colors duration-200 ease-out dark:bg-ink-900">
      <header className="fixed inset-x-0 top-0 z-30 border-b border-ink-200 bg-white px-6 py-4 dark:border-ink-800 dark:bg-ink-900">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setIsSidebarOpen((prev) => !prev)}
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-ink-200 text-ink-700 hover:bg-ink-100 dark:border-ink-700 dark:text-ink-200 dark:hover:bg-ink-800"
              aria-label="Open conversation history"
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
                <path
                  d="M4 7h16M4 12h16M4 17h16"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <h1 className="text-lg font-semibold text-ink-900 dark:text-ink-50">
              Bird Chat
            </h1>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <main className="flex min-h-0 flex-1 flex-col pt-16">
        <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col">
          {hasMessages ? (
            <>
              <div className="flex min-h-0 flex-1">
                <ChatWindow messages={messages} isLoading={isLoading} />
              </div>
              <div className="transition-transform duration-800 ease-out translate-y-0">
                <Composer
                  value={value}
                  image={image}
                  error={error}
                  isSending={isSending}
                  onValueChange={setValue}
                  onImageSelected={handleImageSelected}
                  onRemoveImage={handleRemoveImage}
                  onSend={handleSend}
                />
              </div>
            </>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
              <div className="-translate-y-24">
                <p className="text-3xl font-semibold text-ink-900 dark:text-ink-50">
                  Let&#39;s talk about birds!
                </p>
                <p className="mt-3 text-sm text-ink-500 dark:text-ink-400">
                  Please upload the image of the bird if you need.
                </p>
              </div>
              <div className="mt-4 w-full transition-transform duration-800 ease-out -translate-y-24">
                <Composer
                  value={value}
                  image={image}
                  error={error}
                  isSending={isSending}
                  onValueChange={setValue}
                  onImageSelected={handleImageSelected}
                  onRemoveImage={handleRemoveImage}
                  onSend={handleSend}
                />
              </div>
            </div>
          )}
        </div>
      </main>
      <div
        className={
          "fixed inset-0 z-40 bg-black/40 transition-opacity " +
          (isSidebarOpen ? "opacity-100" : "pointer-events-none opacity-0")
        }
        onClick={() => setIsSidebarOpen(false)}
        aria-hidden
      />
      <aside
        className={
          "fixed left-0 top-0 z-50 h-full w-80 max-w-[85vw] transform border-r border-ink-200 bg-white shadow-xl transition-transform duration-200 dark:border-ink-800 dark:bg-ink-900 " +
          (isSidebarOpen ? "translate-x-0" : "-translate-x-full")
        }
      >
        <div className="flex items-center justify-between border-b border-ink-200 px-4 py-4 dark:border-ink-800">
          <h2 className="text-sm font-semibold text-ink-900 dark:text-ink-50">
            Conversations
          </h2>
          <button
            type="button"
            onClick={() => setIsSidebarOpen(false)}
            className="rounded-lg px-2 py-1 text-sm text-ink-600 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
          >
            Close
          </button>
        </div>
        <div className="flex flex-col gap-3 px-4 py-4">
          <button
            type="button"
            onClick={() => {
              handleStartNewChat();
              setIsSidebarOpen(false);
            }}
            className="rounded-xl border border-ink-200 px-3 py-2 text-sm font-semibold text-ink-900 hover:bg-ink-100 dark:border-ink-700 dark:text-ink-50 dark:hover:bg-ink-800"
          >
            New chat
          </button>
          <div className="flex flex-col gap-2">
            {conversations.length === 0 ? (
              <p className="text-xs text-ink-500 dark:text-ink-400">
                No previous conversations yet.
              </p>
            ) : (
              conversations.map((conv) => (
                <button
                  key={conv.id}
                  type="button"
                  onClick={() => handleSelectConversation(conv.id)}
                  className={
                    "rounded-xl px-3 py-2 text-left text-sm hover:bg-ink-100 dark:hover:bg-ink-800 " +
                    (conv.id === conversationId
                      ? "bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-ink-50"
                      : "text-ink-700 dark:text-ink-300")
                  }
                >
                  <div className="font-semibold">{conv.title}</div>
                  <div className="text-xs text-ink-500 dark:text-ink-400">
                    {new Date(conv.lastUpdatedAt).toLocaleString()}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}
