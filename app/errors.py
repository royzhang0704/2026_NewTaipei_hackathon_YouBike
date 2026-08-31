# 錯誤用列舉碼不用自由文字，下游（儀表板／LLM 措辭）才接得穩。
# 碼表見 meet/20260828/計劃-後端服務.md §4。
class AppError(Exception):
    def __init__(self, code: str, http: int, message: str):
        super().__init__(message)
        self.code, self.http, self.message = code, http, message
