"""SessionBackend：DeepSeek 网页版 的上下文后端。

与 MessagesBackend 的本质差异（用户强调的红线）：
- 网页版**服务端自动记忆**（session_id + parent_message_id 链），
  客户端**只发当轮一条 prompt**，不组装历史、不做提炼。
- 因此本后端极其薄：就是把「极短瞬时状态行 + 用户消息」拼成当轮 prompt，
  直接透传给既有 `DeepSeekClient.chat()`。

历史在服务端，客户端既拿不到、也不该管 —— 没有任何提炼逻辑。
"""

from ai import context as ctx_mod


class SessionBackend:
    """DeepSeek 网页版后端：build 返回当轮 prompt 字符串，request 走现有 chat。"""

    def __init__(self, client):
        self.client = client        # ai.deepseek.DeepSeekClient

    def build(self, ctx, transient, user_msg, system_prompt=None):
        """返回当轮 prompt 字符串（不含历史，历史在服务端）。

        system_prompt 在首次（parent_message_id 为空）由 DeepSeekClient 拼进首条；
        多轮时 client 内部通过 parent 链自带 system，这里不再重复注入。
        """
        prompt = transient.render_session_backend(user_msg)
        return prompt.strip()

    def request(self, prompt, system_prompt=None, model_type=None,
                thinking_enabled=False, search_enabled=False, memory=True,
                ref_file_ids=None):
        """透传网页版 chat（非流式，返回 (content, thinking)）。"""
        return self.client.chat(
            prompt,
            system_prompt=system_prompt,
            model_type=model_type or config_model_type(),
            thinking_enabled=thinking_enabled,
            search_enabled=search_enabled,
            memory=memory,
            ref_file_ids=ref_file_ids,
        )

    def append_history(self, ctx, user_msg, assistant_msg, ephemeral=False):
        """网页版历史在服务端：客户端无需写回任何东西。"""
        return


def config_model_type():
    import config
    return getattr(config, "AI_MODEL_TYPE", "default")
